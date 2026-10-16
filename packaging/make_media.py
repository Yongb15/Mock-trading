"""README용 스크린샷과 시연 GIF를 실제 시세로 다시 만든다.

    pip install pillow   # GIF를 만들 때만 필요(앱 실행에는 필요 없음)
    python packaging/make_media.py

임시 DB(실제 계좌와 무관)에 3주 전에 연 연습 계좌와 거래 몇 건을 만든 뒤, 화면을 차례로 띄워 docs/에 저장한다.
창이 잠깐씩 화면에 떴다 사라진다.
"""
from __future__ import annotations

import sys
import tempfile
import time
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import Image  # noqa: E402
from PySide6.QtCore import QBuffer, QIODevice  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from mock_trading.broker import Broker  # noqa: E402
from mock_trading.domain import Currency, Side, krw  # noqa: E402
from mock_trading.market_data import YFinanceProvider  # noqa: E402
from mock_trading.storage import Repository  # noqa: E402
from mock_trading.ui import main_window as mw  # noqa: E402
from mock_trading.ui.theme import apply_theme  # noqa: E402

DOCS = ROOT / "docs"
SIZE = (1280, 820)
GIF_WIDTH = 960


def settle(app: QApplication, seconds: float) -> None:
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.03)


def to_pil(widget) -> Image.Image:
    buffer = QBuffer()
    buffer.open(QIODevice.ReadWrite)
    widget.grab().save(buffer, "PNG")
    from io import BytesIO

    return Image.open(BytesIO(bytes(buffer.data()))).convert("RGB")


def seed_account(repo: Repository, provider: YFinanceProvider) -> None:
    """3주 전에 2,000만 원으로 연 계좌에, 그날그날의 실제 종가로 거래 몇 건을 남긴다."""
    repo.reset_account(Decimal(20_000_000))
    start = datetime.now() - timedelta(days=21)
    repo._conn.execute("UPDATE account SET created_at = ?", (start.isoformat(timespec="seconds"),))
    closes, fx = provider.get_daily_closes(["005930.KS", "AAPL", "000660.KS"], start.date(), include_usd_krw=True)

    def on(series, day):
        return [v for k, v in sorted(series.items()) if k <= day][-1]

    first = max(min(closes[s]) for s in closes)
    plan = [
        ("005930.KS", Side.BUY, 15, first, Currency.KRW),
        ("AAPL", Side.BUY, 8, first, Currency.USD),
        ("000660.KS", Side.BUY, 3, first + timedelta(days=3), Currency.KRW),
        ("005930.KS", Side.SELL, 5, first + timedelta(days=9), Currency.KRW),
    ]
    names = {symbol: provider.get_quote(symbol).name for symbol in closes}  # 앱이 보여 주는 이름과 같게
    for symbol, side, qty, day, currency in plan:
        name = names[symbol]
        price = on(closes[symbol], day)
        rate = Decimal(1) if currency is Currency.KRW else on(fx, day)
        amount = krw(price * rate * qty)
        fee = (amount * Decimal("0.00015")).quantize(Decimal(1))
        repo.add_trade(symbol=symbol, name=name, side=side, quantity=qty, price=price, currency=currency,
                       fx_rate=rate, amount_krw=amount, fee_krw=fee,
                       executed_at=datetime.combine(day, datetime.min.time()).replace(hour=10))
    for symbol in ["005930.KS", "AAPL", "TSLA", "035720.KS"]:
        quote = provider.get_quote(symbol)
        repo.add_watch(symbol, quote.name, quote.currency, limit=50)


def main() -> int:
    app = QApplication(sys.argv)
    apply_theme(app)
    mw._confirm = lambda *a, **k: True  # 시연에서는 확인창을 자동으로 "예"
    mw._warn = lambda _parent, title, text: print("경고:", title, text, flush=True)  # 막지 않고 기록만
    with tempfile.TemporaryDirectory() as tmp:
        repo = Repository(Path(tmp) / "demo.db", check_same_thread=False)
        provider = YFinanceProvider()
        seed_account(repo, provider)
        print("seeded", flush=True)
        broker = Broker(repo, provider)
        window = mw.MainWindow(broker)
        window.resize(*SIZE)
        window.show()
        window.start()
        settle(app, 6)
        frames: list[tuple[Image.Image, int]] = []

        def shot(name: str | None, hold_ms: int = 1400) -> None:
            print("shot", name, flush=True)
            image = to_pil(window)
            if name:
                image.save(DOCS / name)
            frames.append((image, hold_ms))

        shot("screenshot.png", 1800)                                   # 내 계좌
        window.tabs.setCurrentWidget(window.order_page)
        window.symbol_input.setText("005930")
        window.load_quote()
        settle(app, 6)
        shot(None)
        window.quantity.setValue(3)
        settle(app, 0.5)
        shot(None, 900)
        window.place_order(Side.BUY)                                   # 시장가 매수
        settle(app, 6)
        shot("order-page.png")
        window.order_type.setCurrentIndex(1)                           # 지정가로 걸어 두기
        window.limit_price.setValue(float(window.current.quote.price) * 0.95)
        window.quantity.setValue(5)
        settle(app, 0.5)
        shot(None, 1000)
        window.place_order(Side.BUY)
        settle(app, 5)
        window.tabs.setCurrentWidget(window.orders_tab)
        settle(app, 3)
        shot("limit-order.png")
        window.tabs.setCurrentWidget(window.rank_page)
        settle(app, 12)
        shot("market-ranking.png", 1800)
        window.tabs.setCurrentWidget(window.watch_tab)
        settle(app, 8)
        shot("watchlist.png")
        window.tabs.setCurrentWidget(window.history_tab)
        settle(app, 10)
        shot("asset-history.png", 2200)
        window.tabs.setCurrentWidget(window.account_page)
        settle(app, 4)
        shot(None, 1800)
        window.close()

        images = [f.resize((GIF_WIDTH, int(f.height * GIF_WIDTH / f.width)), Image.LANCZOS) for f, _ in frames]
        palette = [i.quantize(colors=128, method=Image.Quantize.MEDIANCUT) for i in images]
        palette[0].save(DOCS / "demo.gif", save_all=True, append_images=palette[1:],
                        duration=[ms for _, ms in frames], loop=0, optimize=True)
        repo.close()
    print("saved:", sorted(p.name for p in DOCS.iterdir()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
