# Bot de descuentos bruscos (GitHub Actions + Telegram)

Este bot revisa periódicamente ofertas en:

- https://www.mercadolibre.com.pe/
- https://www.falabella.com.pe/falabella-pe
- https://pe.hm.com
- https://www.shopstar.pe/

Guarda un historial de precios y envía alerta por Telegram cuando detecta una caída súbita de **50% o más** entre dos ejecuciones.

## Cómo funciona

1. El workflow de GitHub Actions se ejecuta cada 30 minutos.
2. El script descarga páginas de ofertas y extrae productos + precio actual.
3. Compara con el último precio guardado en caché.
4. Si el precio baja >= 50%, manda mensaje a Telegram.
5. Guarda el nuevo estado para la siguiente ejecución.

> Nota: algunas tiendas cambian su HTML o bloquean scraping por ratos. Si pasa, el bot sigue con los demás sitios y lo reporta en logs.

## Configuración en GitHub

1. Crea un bot con [@BotFather](https://t.me/BotFather) y obtén `TELEGRAM_BOT_TOKEN`.
2. Obtén tu `chat_id` (por ejemplo con `https://api.telegram.org/bot<TOKEN>/getUpdates`).
3. En tu repositorio: **Settings > Secrets and variables > Actions > New repository secret**.
4. Agrega:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
5. Habilita el workflow `.github/workflows/discount-alert-bot.yml`.

## Seguridad de credenciales

- **No pongas el token ni chat_id directamente en código**.
- Usa siempre GitHub Secrets para evitar filtraciones.
- Si ya compartiste el token públicamente, **regénéralo** en BotFather y reemplázalo en tus secrets.

## Ejecución local

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r projects/telegram-discount-bot/requirements.txt
export TELEGRAM_BOT_TOKEN="..."
export TELEGRAM_CHAT_ID="..."
python projects/telegram-discount-bot/bot.py
```

## Ajustes opcionales

- `DROP_THRESHOLD` (default: `50`) porcentaje mínimo de caída.
- `MAX_ITEMS_PER_SITE` (default: `60`) máximo de productos parseados por tienda.
- `MAX_ALERTS_PER_RUN` (default: `20`) límite de alertas por ejecución.
- `REQUEST_TIMEOUT` (default: `20`) timeout HTTP en segundos.
- `STATE_PATH` ruta del JSON de historial.

## Importante (limitaciones reales)

- Este enfoque usa scraping HTML; no es API oficial de las tiendas.
- Si hay contenido cargado solo por JavaScript, puede requerir Playwright/Selenium.
- Si una tienda cambia su HTML, puede requerirse actualizar selectores del parser.
