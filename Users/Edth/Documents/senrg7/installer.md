# Skill: Instalador Windows (.exe) con Inno Setup

## Herramientas requeridas
- **Inno Setup 6** — https://jrsoftware.org/isdl.php (instalar en PC de desarrollo)
- **Python embebido** — https://python.org/downloads → "Windows embeddable package"
- El compilador de Inno Setup: `ISCC.exe` (queda en `C:\Program Files (x86)\Inno Setup 6\`)

## Estructura del instalador
```
installer/
├── senrg7.iss          ← script principal de Inno Setup
├── build.bat           ← doble clic para generar el .exe
├── output/             ← aquí aparece SENRG7_Setup.exe (generado)
└── assets/
    ├── icon.ico        ← ícono de la aplicación (32x32, 48x48, 256x256)
    └── banner.bmp      ← imagen lateral del wizard (164x314 píxeles)
```

## Qué incluye el instalador
El `SENRG7_Setup.exe` instala en `C:\Program Files\SENRG7\`:
```
SENRG7/
├── python-embed/       ← Python embebido (no requiere Python instalado)
├── modulo_1_bridge/    ← código del bridge con dependencias
├── modulo_2_api/       ← código de la API con dependencias y frontend
├── modulo_3_esp32/     ← solo documentación (firmware se sube por Arduino IDE)
├── config.json         ← configuración inicial
├── senrg7.db           ← base de datos (vacía, se crea en primer uso)
├── SENRG7_Bridge.exe   ← acceso directo al bridge
├── SENRG7_API.exe      ← acceso directo a la API
└── Desinstalar SENRG7.exe
```

El instalador también:
- Crea accesos directos en el Escritorio y menú Inicio
- Instala Mosquitto como servicio de Windows (silencioso)
- Configura el firewall de Windows para el puerto 1883 y 8000
- Crea una entrada en "Agregar o quitar programas"

## Proceso de build (paso a paso)

### 1. Preparar Python embebido
```bat
REM Descargar python-3.11.X-embed-amd64.zip de python.org
REM Extraer en installer/python-embed/
REM Habilitar importación de paquetes: editar python311._pth y descomentar import site
```

### 2. Instalar dependencias en el Python embebido
```bat
REM Descargar get-pip.py y correrlo con el Python embebido
installer\python-embed\python.exe get-pip.py
installer\python-embed\python.exe -m pip install paho-mqtt fastapi uvicorn[standard] websockets python-multipart
```

### 3. Generar el instalador
```bat
REM Doble clic en installer/build.bat
REM O desde CMD:
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer\senrg7.iss
```

### 4. Resultado
```
installer/output/SENRG7_Setup.exe   ← este es el archivo a distribuir
```

## Plantilla del script senrg7.iss
```ini
[Setup]
AppName=SENRG7
AppVersion=1.0.0
AppPublisher=ENL Systems
AppPublisherURL=https://senrg7.com
DefaultDirName={autopf}\SENRG7
DefaultGroupName=SENRG7
OutputDir=output
OutputBaseFilename=SENRG7_Setup
SetupIconFile=assets\icon.ico
WizardImageFile=assets\banner.bmp
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
MinVersion=10.0

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "desktopicon"; Description: "Crear icono en el Escritorio"; GroupDescription: "Iconos adicionales:"

[Files]
Source: "..\modulo_1_bridge\*"; DestDir: "{app}\modulo_1_bridge"; Flags: recursesubdirs
Source: "..\modulo_2_api\*";    DestDir: "{app}\modulo_2_api";    Flags: recursesubdirs
Source: "..\config.json";       DestDir: "{app}";                  Flags: onlyifdoesntexist
Source: "python-embed\*";       DestDir: "{app}\python-embed";     Flags: recursesubdirs

[Icons]
Name: "{group}\SENRG7 Dashboard"; Filename: "{app}\iniciar_api.bat"
Name: "{group}\Desinstalar SENRG7"; Filename: "{uninstallexe}"
Name: "{userdesktop}\SENRG7"; Filename: "{app}\iniciar_api.bat"; Tasks: desktopicon

[Run]
Filename: "{app}\scripts\instalar_servicios.bat"; Flags: runhidden; StatusMsg: "Configurando servicios..."

[UninstallRun]
Filename: "{app}\scripts\desinstalar_servicios.bat"; Flags: runhidden
```

## Notas importantes
- El `config.json` usa la flag `onlyifdoesntexist` — al actualizar el instalador no sobreescribe
  la configuración del cliente.
- El Python embebido incluye todas las dependencias — el cliente final NO necesita Python instalado.
- Mosquitto se instala silenciosamente como servicio de Windows y arranca automáticamente.
- El build completo (sin Python embebido cacheado) tarda ~10 minutos.
- El .exe final pesa aproximadamente 50-80MB comprimido.
