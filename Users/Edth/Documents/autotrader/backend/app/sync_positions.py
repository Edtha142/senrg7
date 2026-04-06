"""
Manual position synchronization endpoints.
These are utility endpoints — the main sync runs automatically in TradingEngine.
"""
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models import Position, PositionStatus, Signal, SignalStatus, SignalDirection, TradingConfig, Execution, ExecutionType
from app.async_binance_client import AsyncBinanceClient, BinanceAPIError
from app import state as app_state

logger = logging.getLogger(__name__)
router = APIRouter()


class ImportPositionRequest(BaseModel):
    symbol: str                          # e.g. "ETHUSDT"
    direction: str                       # "LONG" or "SHORT"
    entry_price: float
    quantity: float
    leverage: int
    stop_loss_percent: Optional[float] = None   # uses config default if omitted


async def _get_binance(db: AsyncSession) -> AsyncBinanceClient | None:
    """
    Returns the shared Binance client from the trading engine if available.
    Falls back to creating a new client only for manual /sync and /history endpoints.
    """
    if app_state.trading_engine and app_state.trading_engine.binance:
        return app_state.trading_engine.binance
    # Fallback: read config from DB and create temporary client
    result = await db.execute(select(TradingConfig).limit(1))
    config = result.scalar_one_or_none()
    if not config or not config.binance_api_key:
        return None
    client = AsyncBinanceClient(
        api_key=config.binance_api_key,
        api_secret=config.binance_api_secret,
        testnet=config.binance_testnet,
    )
    await client.sync_time()
    return client


async def _is_shared_client(binance: AsyncBinanceClient) -> bool:
    """True if the client is the shared trading engine instance (must NOT be closed)."""
    return (
        app_state.trading_engine is not None
        and app_state.trading_engine.binance is binance
    )


@router.get("/api/positions/sync")
async def sync_positions_with_binance(db: AsyncSession = Depends(get_db)):
    """Sync DB open positions with current Binance state."""
    binance = await _get_binance(db)
    if not binance:
        return {"success": False, "error": "Binance API keys not configured"}

    owned = not await _is_shared_client(binance)
    try:
        binance_positions = await binance.get_position_info()
    except Exception as e:
        logger.error(f"Sync: Binance request failed: {e}")
        return {"success": False, "error": str(e)}
    finally:
        if owned:
            await binance.close()

    # Map of active positions on Binance by symbol
    active_on_binance: dict[str, dict] = {}
    for bp in binance_positions:
        if abs(float(bp.get("positionAmt", 0))) > 0:
            active_on_binance[bp["symbol"]] = bp

    result = await db.execute(
        select(Position).where(Position.status == PositionStatus.OPEN)
    )
    local_positions = result.scalars().all()

    updated = []

    for pos in local_positions:
        if pos.symbol not in active_on_binance:
            # Closed on Binance — mark in DB
            pos.status = PositionStatus.CLOSED
            pos.closed_at = datetime.now(timezone.utc)
            pos.current_quantity = Decimal("0")

            if pos.signal_id:
                signal = await db.get(Signal, pos.signal_id)
                if signal and signal.status in (SignalStatus.FILLED, SignalStatus.ACTIVE_ORDER):
                    signal.status = SignalStatus.CANCELLED

            updated.append({"symbol": pos.symbol, "action": "closed"})
        else:
            bp = active_on_binance[pos.symbol]
            pos.current_quantity = abs(Decimal(str(bp["positionAmt"])))
            pos.entry_price = Decimal(str(bp["entryPrice"]))
            pos.unrealized_pnl = Decimal(str(bp["unRealizedProfit"]))
            updated.append({
                "symbol": pos.symbol,
                "action": "updated",
                "quantity": float(pos.current_quantity),
                "pnl": float(pos.unrealized_pnl),
            })

    await db.commit()
    return {"success": True, "updated": updated, "timestamp": datetime.now(timezone.utc).isoformat()}


@router.get("/api/positions/live")
async def get_live_positions(db: AsyncSession = Depends(get_db)):
    """
    Get real-time positions directly from Binance with ROE calculation.
    Uses the shared trading engine client — no new HTTP connection overhead.
    """
    binance = await _get_binance(db)
    if not binance:
        return {"success": False, "error": "Binance API keys not configured"}

    owned = not await _is_shared_client(binance)
    try:
        binance_positions = await binance.get_position_info()
    except Exception as e:
        logger.error(f"Live positions: Binance request failed: {e}")
        return {"success": False, "error": "Could not reach Binance", "positions": [], "total_pnl": 0}
    finally:
        if owned:
            await binance.close()

    active = []
    for bp in binance_positions:
        qty = float(bp.get("positionAmt", 0))
        if qty == 0:
            continue

        entry_price = float(bp["entryPrice"])
        mark_price = float(bp["markPrice"])
        leverage = int(bp.get("leverage", 1))

        if entry_price > 0:
            if qty > 0:  # LONG
                roe = ((mark_price - entry_price) / entry_price) * 100 * leverage
            else:  # SHORT
                roe = ((entry_price - mark_price) / entry_price) * 100 * leverage
        else:
            roe = 0

        active.append({
            "symbol": bp["symbol"],
            "side": "LONG" if qty > 0 else "SHORT",
            "quantity": abs(qty),
            "entry_price": entry_price,
            "mark_price": mark_price,
            "liquidation_price": float(bp.get("liquidationPrice", 0)),
            "leverage": leverage,
            "unrealized_pnl": float(bp["unRealizedProfit"]),
            "roe_percent": round(roe, 2),
            "notional": abs(float(bp.get("notional", 0))),
        })

    return {
        "success": True,
        "positions": active,
        "total_pnl": sum(p["unrealized_pnl"] for p in active),
    }


@router.get("/api/trades/history")
async def get_binance_trade_history(days: int = 7, db: AsyncSession = Depends(get_db)):
    """Fetch recent trade history from Binance and sync realized PnL to DB."""
    binance = await _get_binance(db)
    if not binance:
        return {"success": False, "error": "Binance API keys not configured"}

    owned = not await _is_shared_client(binance)
    try:
        end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        start_ms = end_ms - (days * 24 * 60 * 60 * 1000)

        income_data = await binance._request(
            "GET", "/fapi/v1/income",
            signed=True,
            params={"incomeType": "REALIZED_PNL", "startTime": start_ms, "endTime": end_ms, "limit": 1000},
        )
    except Exception as e:
        logger.error(f"Error fetching trade history: {e}", exc_info=True)
        return {"success": False, "error": str(e)}
    finally:
        if owned:
            await binance.close()

    # Group PnL by symbol
    pnl_by_symbol: dict[str, list] = {}
    for entry in (income_data if isinstance(income_data, list) else []):
        symbol = entry.get("symbol", "")
        pnl = float(entry.get("income", 0))
        time_ms = entry.get("time", 0)
        if symbol not in pnl_by_symbol:
            pnl_by_symbol[symbol] = []
        pnl_by_symbol[symbol].append({
            "time": datetime.fromtimestamp(time_ms / 1000, tz=timezone.utc),
            "pnl": pnl,
        })

    # Sync realized PnL to DB
    for symbol, pnl_list in pnl_by_symbol.items():
        total_pnl = sum(p["pnl"] for p in pnl_list)
        if abs(total_pnl) < 0.001:
            continue

        result = await db.execute(
            select(Position)
            .where(Position.symbol == symbol, Position.status == PositionStatus.OPEN)
            .order_by(Position.opened_at.desc())
            .limit(1)
        )
        position = result.scalar_one_or_none()
        if position:
            position.status = PositionStatus.CLOSED
            position.realized_pnl = Decimal(str(total_pnl))
            position.closed_at = pnl_list[-1]["time"]
            position.current_quantity = Decimal("0")

    await db.commit()

    return {
        "success": True,
        "pnl_by_symbol": {
            s: {"total_pnl": sum(p["pnl"] for p in entries), "trades": len(entries)}
            for s, entries in pnl_by_symbol.items()
        },
    }


@router.post("/api/positions/import")
async def import_position(body: ImportPositionRequest, db: AsyncSession = Depends(get_db)):
    """
    Import a manually-opened Binance position into the bot.
    Creates Signal + Position in DB, places SL on Binance, subscribes price stream.
    """
    engine = app_state.trading_engine
    if not engine or not engine.binance:
        raise HTTPException(503, "Trading engine not running")

    symbol = body.symbol.upper()
    direction = body.direction.upper()
    if direction not in ("LONG", "SHORT"):
        raise HTTPException(400, "direction must be LONG or SHORT")

    # Get SL percent from config if not provided
    result = await db.execute(select(TradingConfig).limit(1))
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(503, "TradingConfig not found")

    sl_pct = Decimal(str(body.stop_loss_percent if body.stop_loss_percent else config.stop_loss_percent)) / 100
    entry = Decimal(str(body.entry_price))
    qty = Decimal(str(body.quantity))

    if direction == "LONG":
        sl_price = entry * (1 - sl_pct)
        sl_side = "SELL"
    else:
        sl_price = entry * (1 + sl_pct)
        sl_side = "BUY"

    # Check no duplicate open position for this symbol
    existing = await db.execute(
        select(Position).where(Position.symbol == symbol, Position.status == PositionStatus.OPEN)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(400, f"Already an open position for {symbol}")

    # Place SL on Binance first
    # Binance requires minimum $20 notional — if SL price * qty < $20, raise SL to minimum viable price
    MIN_NOTIONAL = Decimal("20")
    if sl_price * qty < MIN_NOTIONAL:
        min_viable_sl = (MIN_NOTIONAL / qty).quantize(Decimal("0.0001"))
        logger.warning(
            f"SL notional too small for {symbol} ({float(sl_price * qty):.2f} < 20) — "
            f"raising SL to minimum viable price {min_viable_sl}"
        )
        sl_price = min_viable_sl

    sl_order_id = None
    try:
        sl_order = await engine.binance.place_algo_stop(
            symbol=symbol,
            side=sl_side,
            quantity=qty,
            stop_price=sl_price,
        )
        sl_order_id = f"algo:{sl_order.get('algoId', '')}"
        logger.info(f"Import SL placed for {symbol}: stop={sl_price:.4f} id={sl_order_id}")
    except BinanceAPIError as e:
        logger.error(f"Failed to place SL for import {symbol}: {e}")
        raise HTTPException(500, f"Could not place SL on Binance: {e}")

    # Create Signal record (required by FK)
    signal = Signal(
        symbol=symbol,
        direction=SignalDirection(direction),
        entry_price=entry,
        stop_loss_percent=float(sl_pct * 100),
        status=SignalStatus.FILLED,
    )
    db.add(signal)
    await db.flush()  # get signal.id

    # Create Position record
    position = Position(
        signal_id=signal.id,
        symbol=symbol,
        direction=direction,
        entry_price=entry,
        initial_quantity=qty,
        current_quantity=qty,
        leverage=body.leverage,
        stop_loss=sl_price,
        highest_price=entry,
        current_phase="INITIAL",
        status=PositionStatus.OPEN,
        opened_at=datetime.now(timezone.utc),
        binance_stop_order_id=sl_order_id,
    )
    db.add(position)

    # Create entry execution record
    execution = Execution(
        position=position,
        execution_type=ExecutionType.ENTRY,
        quantity=qty,
        price=entry,
        pnl=Decimal("0"),
        binance_order_id="manual_import",
        executed_at=datetime.now(timezone.utc),
    )
    db.add(execution)
    await db.commit()

    # Subscribe price stream so trailing activates when profit >= 1%
    if engine.price_stream:
        engine.price_stream.subscribe(symbol)

    logger.info(f"Position imported: {symbol} {direction} entry={entry} qty={qty} SL={sl_price:.4f}")
    return {
        "success": True,
        "symbol": symbol,
        "direction": direction,
        "entry_price": float(entry),
        "quantity": float(qty),
        "stop_loss": float(sl_price),
        "sl_order_id": sl_order_id,
    }
