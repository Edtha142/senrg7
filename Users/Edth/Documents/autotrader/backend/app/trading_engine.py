"""
AutoTrader Pro — Consolidated Trading Engine

Single engine that manages the full lifecycle of a trade:

1. On entry fill  → create Position, place initial STOP_MARKET, subscribe price stream
2. On price tick  → track highest price; if profit >= 1% activate trailing SL
3. Trailing SL    → ratcheting STOP_MARKET on Binance: on each new high, cancel old SL
                    and place new one at highest_price * (1 - callback%). Binance executes
                    natively. Price stream stays subscribed for continuous tracking.
4. On SL fill     → close Position, record Execution + PnL, notify
5. Sync loop      → every 30s backup sync with Binance (catches edge cases, verifies SL)
"""
import asyncio
import logging
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.async_binance_client import AsyncBinanceClient, BinanceAPIError
from app.binance_websocket import BinanceUserStream
from app.price_stream import PriceStream
from app.models import (
    Signal, SignalStatus,
    Position, PositionStatus,
    Execution, ExecutionType,
    TradingConfig,
)
from app.core.websocket import manager as ws_manager

logger = logging.getLogger(__name__)

# Profit threshold to activate trailing stop (1% = breakeven + trail begins)
TRAILING_THRESHOLD_PCT = Decimal("1.0")


class TradingEngine:
    """
    Consolidated trading engine. Instantiated once at app startup.
    Reads Binance credentials from TradingConfig in the database,
    so all settings are configurable from the dashboard.
    """

    def __init__(self, session_factory: async_sessionmaker):
        self.session_factory = session_factory
        self.binance: Optional[AsyncBinanceClient] = None
        self.user_stream: Optional[BinanceUserStream] = None
        self.price_stream: Optional[PriceStream] = None
        self._running = False
        self._sync_task: Optional[asyncio.Task] = None
        self._config: Optional[TradingConfig] = None

    # ─── Lifecycle ────────────────────────────────────────────────────

    async def start(self) -> None:
        self._running = True
        logger.info("Trading Engine starting...")

        config = await self._load_config()
        if config is None:
            logger.warning("No TradingConfig found — engine idle until configured")
            return

        if not config.binance_api_key or not config.binance_api_secret:
            logger.warning("Binance API keys not configured — engine idle")
            return

        await self._initialize(config)

    async def _initialize(self, config: TradingConfig) -> None:
        """Initialize Binance client, WebSocket streams and sync loop."""
        self._config = config

        self.binance = AsyncBinanceClient(
            api_key=config.binance_api_key,
            api_secret=config.binance_api_secret,
            testnet=config.binance_testnet,
        )
        await self.binance.sync_time()  # sincronizar reloj con Binance al arrancar

        self.price_stream = PriceStream(
            on_price_update=self._on_price_update,
            testnet=config.binance_testnet,
        )

        self.user_stream = BinanceUserStream(
            binance_client=self.binance,
            on_entry_fill=self._on_entry_fill,
            on_sl_fill=self._on_sl_fill,
        )

        # Re-subscribe price streams for any OPEN positions in INITIAL phase
        await self._restore_price_streams()

        await self.user_stream.start()
        self._sync_task = asyncio.create_task(self._sync_loop(), name="engine_sync")
        logger.info(
            f"Trading Engine ready | "
            f"{'TESTNET' if config.binance_testnet else 'PRODUCTION'} | "
            f"SL={float(config.stop_loss_percent)}% | "
            f"Size={float(config.position_size_usd)} USDT | "
            f"Leverage={config.leverage}x"
        )

    async def stop(self) -> None:
        self._running = False
        if self.user_stream:
            await self.user_stream.stop()
        if self.price_stream:
            await self.price_stream.stop()
        if self._sync_task and not self._sync_task.done():
            self._sync_task.cancel()
        if self.binance:
            await self.binance.close()
        logger.info("Trading Engine stopped")

    async def restart(self) -> None:
        """Call this after updating TradingConfig to reload credentials."""
        logger.info("Trading Engine restarting with new config...")
        await self.stop()
        await asyncio.sleep(1)
        self._running = True
        config = await self._load_config()
        if config and config.binance_api_key:
            await self._initialize(config)

    # ─── Configuration ────────────────────────────────────────────────

    async def _load_config(self) -> Optional[TradingConfig]:
        async with self.session_factory() as session:
            result = await session.execute(select(TradingConfig).limit(1))
            return result.scalar_one_or_none()

    async def _get_config(self) -> TradingConfig:
        """Get fresh config from DB (always reads latest values)."""
        config = await self._load_config()
        if config is None:
            raise RuntimeError("TradingConfig not found in database")
        return config

    # ─── Entry fill handler ───────────────────────────────────────────

    async def _on_entry_fill(self, event: dict) -> None:
        """
        Called when a LIMIT entry order is filled by Binance.
        Creates the Position in DB, places initial fixed STOP_MARKET.
        """
        symbol = event["symbol"]
        order_id = event["order_id"]
        side = event["side"]           # BUY (LONG) or SELL (SHORT)
        fill_price = Decimal(str(event["avg_price"]))
        quantity = Decimal(str(event["executed_qty"]))

        async with self.session_factory() as session:
            # Find the signal that originated this order
            signal = await self._get_signal_by_order_id(session, order_id)
            if signal is None:
                logger.warning(f"No signal found for order_id={order_id} — skipping")
                return

            config = await self._get_config()
            sl_pct = Decimal(str(config.stop_loss_percent)) / 100

            # Calculate initial stop loss price
            if side == "BUY":  # LONG
                sl_price = fill_price * (1 - sl_pct)
                direction = "LONG"
                sl_side = "SELL"
            else:               # SHORT
                sl_price = fill_price * (1 + sl_pct)
                direction = "SHORT"
                sl_side = "BUY"

            # Create Position record
            position = Position(
                signal_id=signal.id,
                symbol=symbol,
                direction=direction,
                entry_price=fill_price,
                initial_quantity=quantity,
                current_quantity=quantity,
                leverage=config.leverage,
                stop_loss=sl_price,
                highest_price=fill_price,
                current_phase="INITIAL",
                status=PositionStatus.OPEN,
                opened_at=datetime.now(timezone.utc),
            )
            session.add(position)

            # Place initial fixed STOP_MARKET on Binance
            try:
                sl_order = await self.binance.place_stop_market(
                    symbol=symbol,
                    side=sl_side,
                    quantity=quantity,
                    stop_price=sl_price,
                )
                position.binance_stop_order_id = str(sl_order["orderId"])
            except BinanceAPIError as e:
                if e.code == -4120:
                    # Account requires algo order endpoints
                    logger.warning(f"Account requires algo orders (-4120), switching to algo stop for {symbol}")
                    self.binance._use_algo_orders = True
                    try:
                        sl_order = await self.binance.place_algo_stop(
                            symbol=symbol,
                            side=sl_side,
                            quantity=quantity,
                            stop_price=sl_price,
                        )
                        algo_id = str(sl_order.get("algoId", ""))
                        position.binance_stop_order_id = f"algo:{algo_id}"
                    except BinanceAPIError as algo_e:
                        logger.error(f"Failed to place algo SL for {symbol}: {algo_e}")
                        position.current_phase = "SL_FAILED"
                else:
                    logger.error(f"Failed to place initial SL for {symbol}: {e}")
                    position.current_phase = "SL_FAILED"

            # Record entry execution
            entry_exec = Execution(
                position=position,
                execution_type=ExecutionType.ENTRY,
                quantity=quantity,
                price=fill_price,
                pnl=Decimal("0"),
                binance_order_id=order_id,
                executed_at=datetime.now(timezone.utc),
            )
            session.add(entry_exec)

            # Update signal status
            signal.status = SignalStatus.FILLED
            await session.commit()

            logger.info(
                f"Position opened: {symbol} {direction} | "
                f"entry={fill_price} | qty={quantity} | "
                f"SL={sl_price} ({config.stop_loss_percent}%)"
            )

        # Subscribe price stream to monitor for trailing stop activation
        if self.price_stream and position.current_phase == "INITIAL":
            self.price_stream.subscribe(symbol)

        # Broadcast to dashboard
        await ws_manager.broadcast({
            "type": "position_opened",
            "data": {
                "symbol": symbol,
                "direction": direction,
                "entry_price": float(fill_price),
                "quantity": float(quantity),
                "stop_loss": float(sl_price),
            },
        })

        # Send notification
        await self._notify_position_opened(symbol, direction, fill_price, quantity, sl_price)

    # ─── Price update handler ─────────────────────────────────────────

    async def _on_price_update(self, symbol: str, mark_price: Decimal) -> None:
        """
        Called every ~1 second for each symbol in INITIAL phase.
        Checks if profit >= 1% to switch to native TRAILING_STOP_MARKET.
        """
        if not self._running:
            return

        async with self.session_factory() as session:
            positions = await self._get_open_positions_for_symbol(session, symbol)

            for position in positions:
                if position.current_phase not in ("INITIAL", "TRAILING_MANUAL"):
                    continue

                entry = Decimal(str(position.entry_price))

                # Calculate unrealized profit %
                if position.direction == "LONG":
                    profit_pct = (mark_price - entry) / entry * 100
                else:
                    profit_pct = (entry - mark_price) / entry * 100

                # Update highest_price tracking
                prev_highest = Decimal(str(position.highest_price or entry))
                new_high = mark_price > prev_highest
                if new_high:
                    position.highest_price = mark_price
                current_highest = mark_price if new_high else prev_highest

                if position.current_phase == "INITIAL":
                    # Check trailing threshold — switch to trailing stop
                    if profit_pct >= TRAILING_THRESHOLD_PCT:
                        await self._activate_trailing_stop(session, position, mark_price)

                elif position.current_phase == "TRAILING_MANUAL":
                    # Manual trailing: if price made a new high, move the SL up
                    if new_high:
                        config = await self._get_config()
                        callback_rate = Decimal(str(config.stop_loss_percent))
                        sl_side = "SELL" if position.direction == "LONG" else "BUY"
                        quantity = Decimal(str(position.current_quantity))
                        new_sl = current_highest * (1 - callback_rate / 100)
                        old_order_id = position.binance_stop_order_id

                        # Place NEW SL first — only cancel old one after new is confirmed
                        try:
                            sl_order = await self.binance.place_algo_stop(
                                symbol=symbol,
                                side=sl_side,
                                quantity=quantity,
                                stop_price=new_sl,
                            )
                            algo_id = str(sl_order.get("algoId", ""))
                            position.binance_stop_order_id = f"algo:{algo_id}"
                            position.stop_loss = new_sl
                            logger.info(
                                f"Manual trailing SL moved: {symbol} | "
                                f"highest={current_highest} | new_SL={new_sl:.4f} | "
                                f"algoId={algo_id}"
                            )
                            # Cancel old SL only after new one is placed
                            if old_order_id:
                                await self._cancel_sl_order(symbol, old_order_id)
                        except BinanceAPIError as e:
                            logger.error(f"Failed to update trailing SL for {symbol}: {e}")

                await session.commit()

    async def _cancel_sl_order(self, symbol: str, stop_order_id: str) -> None:
        """
        Cancel an SL order, routing to cancel_algo_order if it's an algo order
        (identified by the 'algo:' prefix on the stored order ID).
        """
        if not stop_order_id:
            return
        try:
            if stop_order_id.startswith("algo:"):
                strategy_id = stop_order_id[len("algo:"):]
                await self.binance.cancel_algo_order(symbol, strategy_id)
            else:
                await self.binance.cancel_order(symbol, stop_order_id)
        except BinanceAPIError as e:
            logger.warning(f"Could not cancel SL order {stop_order_id} for {symbol}: {e}")

    async def _activate_trailing_stop(
        self, session: AsyncSession, position: Position, current_price: Decimal,
        *, commit: bool = True
    ) -> None:
        """
        Switch from fixed STOP_MARKET to ratcheting manual trailing SL.

        Cancels the initial fixed SL and places a new STOP_MARKET at
        current_price * (1 - callback%). On each new price high, _on_price_update
        moves the SL up automatically. Binance executes the SL natively.
        Price stream stays subscribed — no algo trailing orders used.
        """
        symbol = position.symbol
        config = await self._get_config()
        callback_rate = Decimal(str(config.stop_loss_percent))
        sl_side = "SELL" if position.direction == "LONG" else "BUY"
        quantity = Decimal(str(position.current_quantity))
        new_sl = current_price * (1 - callback_rate / 100)

        logger.info(
            f"Activating trailing SL: {symbol} | "
            f"price={current_price} | first_SL={new_sl:.4f} | trail={callback_rate}%"
        )

        old_order_id = position.binance_stop_order_id

        # Place new SL FIRST, then cancel old one — never leave position without SL
        try:
            sl_order = await self.binance.place_algo_stop(
                symbol=symbol,
                side=sl_side,
                quantity=quantity,
                stop_price=new_sl,
            )
            position.binance_stop_order_id = f"algo:{sl_order.get('algoId', '')}"
            position.current_phase = "TRAILING_MANUAL"
            # Cancel old SL only after new one confirmed
            if old_order_id:
                await self._cancel_sl_order(symbol, old_order_id)
            position.stop_loss = new_sl
            # Price stream stays subscribed — _on_price_update handles SL moves
            if commit:
                await session.commit()

            logger.info(
                f"Trailing SL placed: {symbol} | "
                f"SL={new_sl:.4f} | order={position.binance_stop_order_id}"
            )
            await ws_manager.broadcast({
                "type": "trailing_stop_activated",
                "data": {
                    "symbol": symbol,
                    "activation_price": float(current_price),
                    "callback_rate": float(callback_rate),
                },
            })

        except BinanceAPIError as e:
            logger.error(f"Failed to place trailing SL for {symbol}: {e}")

    # ─── SL fill handler ──────────────────────────────────────────────

    async def _on_sl_fill(self, event: dict) -> None:
        """
        Called when a STOP_MARKET or TRAILING_STOP_MARKET is filled.
        Closes the position, records PnL, sends notification.
        """
        symbol = event["symbol"]
        order_id = event["order_id"]
        fill_price = Decimal(str(event["avg_price"]))
        order_type = event["order_type"]

        position_side = event.get("position_side", "BOTH")  # LONG / SHORT / BOTH
        # Map Binance positionSide to our direction
        direction_map = {"LONG": "LONG", "SHORT": "SHORT", "BOTH": None}
        expected_direction = direction_map.get(position_side)

        async with self.session_factory() as session:
            # Primary: search by stored stop order id (works for regular orders)
            position = await self._get_position_by_stop_order_id(session, order_id)

            if position is None:
                # Fallback: algo orders report a different order_id than the algoId we stored.
                # Use symbol + direction (from positionSide) to find the correct position.
                position = await self._get_open_position_by_symbol_and_direction(
                    session, symbol, expected_direction
                )

            if position is None:
                logger.warning(f"No open position found for SL fill: {symbol} order={order_id} side={position_side}")
                return

            entry = Decimal(str(position.entry_price))
            quantity = Decimal(str(position.current_quantity))

            # Calculate PnL
            if position.direction == "LONG":
                pnl = (fill_price - entry) * quantity
            else:
                pnl = (entry - fill_price) * quantity

            # Record execution
            exec_type = ExecutionType.STOP_LOSS
            execution = Execution(
                position_id=position.id,
                execution_type=exec_type,
                quantity=quantity,
                price=fill_price,
                pnl=pnl,
                binance_order_id=order_id,
                executed_at=datetime.now(timezone.utc),
            )
            session.add(execution)

            # Cancel backup SL/TP order if one exists (the other order that didn't fill)
            backup_order_id = position.binance_tp_order_id or position.binance_stop_order_id
            # Whichever filled is `order_id`; cancel the other one
            other_order = None
            if position.binance_tp_order_id and position.binance_stop_order_id:
                # One filled — cancel the other
                filled_id_clean = order_id  # regular order_id from WebSocket
                if position.binance_stop_order_id != f"algo:{order_id}" and position.binance_stop_order_id != order_id:
                    other_order = position.binance_stop_order_id
                elif position.binance_tp_order_id != f"algo:{order_id}" and position.binance_tp_order_id != order_id:
                    other_order = position.binance_tp_order_id
            if other_order:
                try:
                    await self._cancel_sl_order(symbol, other_order)
                    logger.info(f"Cancelled backup order after fill: {symbol} order={other_order}")
                except Exception as cancel_err:
                    logger.warning(f"Could not cancel backup order {other_order}: {cancel_err}")

            # Close position
            position.status = PositionStatus.CLOSED
            position.realized_pnl = pnl
            position.current_quantity = Decimal("0")
            position.closed_at = datetime.now(timezone.utc)
            position.binance_stop_order_id = None
            position.binance_tp_order_id = None

            await session.commit()

            logger.info(
                f"Position closed: {symbol} | "
                f"type={order_type} | close={fill_price} | "
                f"PnL={pnl:.4f} USDT"
            )

        # Clean up price stream if it's still running for this symbol
        if self.price_stream:
            self.price_stream.unsubscribe(symbol)

        await ws_manager.broadcast({
            "type": "position_closed",
            "data": {
                "symbol": symbol,
                "close_price": float(fill_price),
                "pnl": float(pnl),
                "order_type": order_type,
            },
        })

        await self._notify_position_closed(symbol, position.direction, entry, fill_price, pnl)

    # ─── Sync loop ────────────────────────────────────────────────────

    async def _sync_loop(self) -> None:
        """
        Backup sync every 30 seconds.
        Catches positions that were closed without us receiving a WebSocket event
        (e.g. reconnection gaps, liquidations, manual closes on Binance).
        """
        while self._running:
            await asyncio.sleep(30)
            if not self._running:
                return
            try:
                await self._sync_with_binance()
            except Exception as e:
                logger.error(f"Sync loop error: {e}", exc_info=True)

    async def _sync_with_binance(self) -> None:
        """Compare DB open positions with Binance actual positions."""
        if not self.binance:
            return

        await self.binance.sync_time()  # re-sincronizar reloj antes de peticiones firmadas
        binance_positions = await self.binance.get_position_info()

        # Build set of active symbols on Binance
        active_on_binance: dict[str, dict] = {}
        for bp in binance_positions:
            if abs(float(bp.get("positionAmt", 0))) > 0:
                active_on_binance[bp["symbol"]] = bp

        async with self.session_factory() as session:
            result = await session.execute(
                select(Position).where(Position.status == PositionStatus.OPEN)
            )
            open_positions = result.scalars().all()

            for position in open_positions:
                if position.symbol not in active_on_binance:
                    # Position closed on Binance but still OPEN in DB
                    # (could be liquidation or closed outside the bot)
                    logger.warning(
                        f"Position {position.symbol} not found on Binance — marking CLOSED"
                    )
                    position.status = PositionStatus.CLOSED
                    position.closed_at = datetime.now(timezone.utc)
                    if self.price_stream:
                        self.price_stream.unsubscribe(position.symbol)
                    await ws_manager.broadcast({
                        "type": "position_closed",
                        "data": {"symbol": position.symbol, "reason": "liquidation_or_manual"},
                    })
                else:
                    # Update unrealized PnL from Binance
                    bp = active_on_binance[position.symbol]
                    position.unrealized_pnl = Decimal(str(bp.get("unRealizedProfit", 0)))
                    position.current_quantity = abs(Decimal(str(bp.get("positionAmt", 0))))

                    # Every sync cycle: verify SL still exists on Binance
                    if position.current_phase in ("INITIAL", "TRAILING_MANUAL"):
                        await self._verify_and_repair_sl(session, position)

            await session.commit()

    async def _restore_price_streams(self) -> None:
        """On startup, re-subscribe price streams for INITIAL/TRAILING_MANUAL positions and retry SL_FAILED ones."""
        async with self.session_factory() as session:
            result = await session.execute(
                select(Position).where(
                    Position.status == PositionStatus.OPEN,
                    Position.current_phase.in_(["INITIAL", "SL_FAILED", "TRAILING_MANUAL", "TRAILING"]),
                )
            )
            positions = result.scalars().all()

            for pos in positions:
                if pos.current_phase == "TRAILING":
                    # Migrate legacy TRAILING phase → TRAILING_MANUAL (ratcheting SL approach)
                    logger.info(f"Migrating {pos.symbol} from TRAILING → TRAILING_MANUAL")
                    if pos.binance_stop_order_id:
                        await self._cancel_sl_order(pos.symbol, pos.binance_stop_order_id)
                        pos.binance_stop_order_id = None
                    if pos.binance_tp_order_id:
                        await self._cancel_sl_order(pos.symbol, pos.binance_tp_order_id)
                        pos.binance_tp_order_id = None

                    config = await self._get_config()
                    sl_pct = Decimal(str(config.stop_loss_percent)) / 100
                    highest = Decimal(str(pos.highest_price or pos.entry_price))
                    sl_side = "SELL" if pos.direction.value == "LONG" else "BUY"
                    new_sl = highest * (1 - sl_pct)
                    qty = Decimal(str(pos.current_quantity))
                    try:
                        sl_order = await self.binance.place_algo_stop(
                            symbol=pos.symbol, side=sl_side,
                            quantity=qty, stop_price=new_sl,
                        )
                        pos.binance_stop_order_id = f"algo:{sl_order.get('algoId', '')}"
                        pos.current_phase = "TRAILING_MANUAL"
                        pos.stop_loss = new_sl
                        await session.commit()
                        if self.price_stream:
                            self.price_stream.subscribe(pos.symbol)
                        logger.info(
                            f"Migrated {pos.symbol} → TRAILING_MANUAL | "
                            f"SL={new_sl:.4f} (highest={highest})"
                        )
                    except Exception as e:
                        logger.error(f"Could not migrate {pos.symbol} to TRAILING_MANUAL: {e}")

                elif pos.current_phase in ("INITIAL", "TRAILING_MANUAL"):
                    # Verify SL actually exists on Binance — repair if missing
                    await self._verify_and_repair_sl(session, pos)
                    if self.price_stream:
                        self.price_stream.subscribe(pos.symbol)
                        logger.info(f"Restored price stream for {pos.symbol} ({pos.current_phase})")

                elif pos.current_phase == "SL_FAILED":
                    # Retry placing the initial stop loss
                    sl_side = "SELL" if pos.direction.value == "LONG" else "BUY"
                    try:
                        sl_price = Decimal(str(pos.entry_price)) * (
                            1 - Decimal(str(self._config.stop_loss_percent)) / 100
                        )
                        quantity = Decimal(str(pos.current_quantity))
                        try:
                            sl_order = await self.binance.place_stop_market(
                                symbol=pos.symbol,
                                side=sl_side,
                                quantity=quantity,
                                stop_price=sl_price,
                            )
                            pos.binance_stop_order_id = str(sl_order.get("orderId", ""))
                            pos.current_phase = "INITIAL"
                            logger.info(f"SL recovered for {pos.symbol}: stop={sl_price:.4f}, phase->INITIAL")
                        except BinanceAPIError as e:
                            if e.code == -4120:
                                self.binance._use_algo_orders = True
                                sl_order = await self.binance.place_algo_stop(
                                    symbol=pos.symbol,
                                    side=sl_side,
                                    quantity=quantity,
                                    stop_price=sl_price,
                                )
                                algo_id = str(sl_order.get("algoId", ""))
                                pos.binance_stop_order_id = f"algo:{algo_id}"
                                pos.current_phase = "INITIAL"
                                logger.info(
                                    f"SL recovered (algo stop) for {pos.symbol}: "
                                    f"stop={sl_price:.4f}, algoId={algo_id}, phase->INITIAL"
                                )
                            else:
                                raise
                        await session.commit()
                        if self.price_stream:
                            self.price_stream.subscribe(pos.symbol)
                    except Exception as e:
                        logger.error(f"Could not recover SL for {pos.symbol}: {e}", exc_info=True)

    # ─── SL integrity ─────────────────────────────────────────────────

    async def _verify_and_repair_sl(self, session: AsyncSession, pos: Position) -> None:
        """
        Check that the SL order stored in binance_stop_order_id actually exists on Binance.
        If it has disappeared (cancelled externally, expired, etc.), place a new one.
        Called at startup and every sync cycle.
        """
        if not pos.binance_stop_order_id:
            sl_found = False
        elif pos.binance_stop_order_id.startswith("algo:"):
            stored_id = pos.binance_stop_order_id.replace("algo:", "")
            try:
                open_orders = await self.binance.get_open_algo_orders(pos.symbol)
                sl_found = any(str(o.get("algoId", "")) == stored_id for o in open_orders)
            except Exception as e:
                logger.error(f"Cannot verify SL for {pos.symbol}: {e}")
                return  # skip repair if Binance is unreachable
        else:
            # Regular order — assume present (rare for this account)
            return

        if sl_found:
            return  # all good

        logger.warning(
            f"SL order missing on Binance for {pos.symbol} "
            f"(stored={pos.binance_stop_order_id}) — placing new SL"
        )

        config = await self._get_config()
        sl_pct = Decimal(str(config.stop_loss_percent)) / 100
        sl_side = "SELL" if pos.direction.value == "LONG" else "BUY"
        qty = Decimal(str(pos.current_quantity))
        entry = Decimal(str(pos.entry_price))
        # If already trailing, use highest_price as base — not entry
        base = Decimal(str(pos.highest_price)) if pos.highest_price and pos.current_phase == "TRAILING_MANUAL" else entry
        original_sl = base * (1 - sl_pct) if pos.direction.value == "LONG" else base * (1 + sl_pct)

        # Check current mark price — if already past SL, place emergency SL near current price
        try:
            current = await self.binance.get_mark_price(pos.symbol)
        except Exception:
            current = original_sl  # fallback

        if pos.direction.value == "LONG" and current < original_sl:
            sl_price = current * Decimal("0.998")  # 0.2% below — limits further loss
            logger.warning(
                f"Price {current:.4f} already below original SL {original_sl:.4f} "
                f"for {pos.symbol} — emergency SL at {sl_price:.4f}"
            )
        elif pos.direction.value == "SHORT" and current > original_sl:
            sl_price = current * Decimal("1.002")
            logger.warning(
                f"Price {current:.4f} already above original SL {original_sl:.4f} "
                f"for {pos.symbol} — emergency SL at {sl_price:.4f}"
            )
        else:
            sl_price = original_sl

        try:
            sl_order = await self.binance.place_algo_stop(
                symbol=pos.symbol, side=sl_side, quantity=qty, stop_price=sl_price,
            )
            algo_id = str(sl_order.get("algoId", ""))
            pos.binance_stop_order_id = f"algo:{algo_id}"
            # Keep TRAILING_MANUAL phase if it was already trailing
            if pos.current_phase != "TRAILING_MANUAL":
                pos.current_phase = "INITIAL"
            pos.stop_loss = sl_price
            await session.commit()
            logger.info(
                f"SL repaired for {pos.symbol}: stop={sl_price:.4f} "
                f"algoId={algo_id} phase={pos.current_phase} (was missing)"
            )
        except Exception as e:
            logger.error(f"Failed to repair SL for {pos.symbol}: {e}")
            pos.current_phase = "SL_FAILED"
            await session.commit()

    # ─── DB helpers ───────────────────────────────────────────────────

    async def _get_signal_by_order_id(
        self, session: AsyncSession, order_id: str
    ) -> Optional[Signal]:
        result = await session.execute(
            select(Signal).where(Signal.binance_order_id == order_id)
        )
        return result.scalar_one_or_none()

    async def _get_position_by_stop_order_id(
        self, session: AsyncSession, order_id: str
    ) -> Optional[Position]:
        result = await session.execute(
            select(Position).where(
                Position.binance_stop_order_id == order_id,
                Position.status == PositionStatus.OPEN,
            )
        )
        return result.scalar_one_or_none()

    async def _get_open_positions_for_symbol(
        self, session: AsyncSession, symbol: str
    ) -> list[Position]:
        result = await session.execute(
            select(Position).where(
                Position.symbol == symbol,
                Position.status == PositionStatus.OPEN,
            )
        )
        return result.scalars().all()

    async def _get_open_position_by_symbol_and_direction(
        self, session: AsyncSession, symbol: str, direction: Optional[str]
    ) -> Optional[Position]:
        """Find open position by symbol + direction (LONG/SHORT). Falls back to any if direction is None."""
        filters = [Position.symbol == symbol, Position.status == PositionStatus.OPEN]
        if direction:
            filters.append(Position.direction == direction)
        result = await session.execute(
            select(Position).where(*filters).limit(1)
        )
        return result.scalar_one_or_none()

    # ─── Notifications ────────────────────────────────────────────────

    async def _notify_position_opened(
        self,
        symbol: str,
        direction: str,
        entry: Decimal,
        qty: Decimal,
        sl: Decimal,
    ) -> None:
        try:
            from app.notification_service import NotificationService
            notifier = NotificationService()
            msg = (
                f"<b>Posición Abierta</b>\n"
                f"{'🟢 LONG' if direction == 'LONG' else '🔴 SHORT'} {symbol}\n"
                f"Entrada: <code>{float(entry):.4f}</code>\n"
                f"Cantidad: <code>{float(qty)}</code>\n"
                f"Stop Loss: <code>{float(sl):.4f}</code>"
            )
            await notifier.notify_all(msg)
        except Exception as e:
            logger.warning(f"Notification error: {e}")

    async def _notify_position_closed(
        self,
        symbol: str,
        direction: str,
        entry: Decimal,
        close: Decimal,
        pnl: Decimal,
    ) -> None:
        try:
            from app.notification_service import NotificationService
            notifier = NotificationService()
            emoji = "✅" if pnl >= 0 else "❌"
            msg = (
                f"<b>{emoji} Posición Cerrada</b>\n"
                f"{'🟢 LONG' if direction == 'LONG' else '🔴 SHORT'} {symbol}\n"
                f"Entrada: <code>{float(entry):.4f}</code>\n"
                f"Cierre:  <code>{float(close):.4f}</code>\n"
                f"PnL: <b>{float(pnl):+.4f} USDT</b>"
            )
            await notifier.notify_all(msg)
        except Exception as e:
            logger.warning(f"Notification error: {e}")


# ─── Public API for placing orders (called from signals endpoint) ──────────

async def place_signal_order(
    session: AsyncSession,
    signal: Signal,
    binance: AsyncBinanceClient,
) -> None:
    """
    Place a LIMIT order on Binance for a given signal.
    Updates signal.binance_order_id and signal.status.
    Called directly from POST /api/v1/signals.
    """
    config_result = await session.execute(select(TradingConfig).limit(1))
    config = config_result.scalar_one_or_none()
    if config is None:
        raise RuntimeError("TradingConfig not found")

    entry_price = Decimal(str(signal.entry_price))
    side = "BUY" if signal.direction == "LONG" else "SELL"

    quantity = await binance.calculate_quantity(
        symbol=signal.symbol,
        position_size_usd=Decimal(str(config.position_size_usd)),
        entry_price=entry_price,
        leverage=config.leverage,
    )

    order = await binance.place_limit_order(
        symbol=signal.symbol,
        side=side,
        quantity=quantity,
        price=entry_price,
        leverage=config.leverage,
    )

    signal.binance_order_id = str(order["orderId"])
    signal.status = SignalStatus.ACTIVE_ORDER
