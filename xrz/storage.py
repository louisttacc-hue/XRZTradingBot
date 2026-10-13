"""SQLite-Speicher: Trades, Kontostand-Verlauf, Wissen, Chat, Zustände."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mode TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    qty REAL NOT NULL,
    entry_price REAL NOT NULL,
    entry_time REAL NOT NULL,
    stop REAL,
    initial_stop REAL,
    take_profit REAL,
    exit_price REAL,
    exit_time REAL,
    pnl REAL,
    pnl_pct REAL,
    r_multiple REAL,
    fees REAL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'open',
    entry_reason TEXT,
    exit_reason TEXT,
    score REAL,
    strategies TEXT,
    ai_comment TEXT,
    meta TEXT
);
CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status, mode);

CREATE TABLE IF NOT EXISTS equity (
    ts REAL NOT NULL,
    mode TEXT NOT NULL,
    equity REAL NOT NULL,
    cash REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_equity ON equity(mode, ts);

CREATE TABLE IF NOT EXISTS knowledge (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    source TEXT,
    topic TEXT,
    text TEXT NOT NULL,
    embedding TEXT,
    importance REAL DEFAULT 1.0
);

CREATE TABLE IF NOT EXISTS chat (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    channel TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chat ON chat(channel, id);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    level TEXT NOT NULL,
    kind TEXT,
    message TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS strategy_stats (
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    strategy TEXT NOT NULL,
    updated REAL NOT NULL,
    params TEXT,
    metrics TEXT,
    weight REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (symbol, timeframe, strategy)
);

CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    source TEXT NOT NULL,
    symbol TEXT NOT NULL,
    action TEXT NOT NULL,
    payload TEXT
);

CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


@dataclass
class Trade:
    mode: str
    symbol: str
    side: str  # "long" | "short"
    qty: float
    entry_price: float
    entry_time: float
    stop: float | None = None
    initial_stop: float | None = None
    take_profit: float | None = None
    exit_price: float | None = None
    exit_time: float | None = None
    pnl: float | None = None
    pnl_pct: float | None = None
    r_multiple: float | None = None
    fees: float = 0.0
    status: str = "open"
    entry_reason: str = ""
    exit_reason: str = ""
    score: float = 0.0
    strategies: dict[str, float] = field(default_factory=dict)
    ai_comment: str = ""
    meta: dict[str, Any] = field(default_factory=dict)
    id: int | None = None

    @property
    def direction(self) -> int:
        return 1 if self.side == "long" else -1

    def unrealized(self, price: float) -> float:
        return (price - self.entry_price) * self.qty * self.direction

    def risk_per_unit(self) -> float:
        ref = self.initial_stop if self.initial_stop else self.stop
        if not ref:
            return 0.0
        return abs(self.entry_price - ref)

    def current_r(self, price: float) -> float:
        rpu = self.risk_per_unit()
        if rpu <= 0:
            return 0.0
        return (price - self.entry_price) * self.direction / rpu


class Storage:
    def __init__(self, path: Path | str):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(SCHEMA)
            if self.path != ":memory:":
                self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.commit()

    # ------------------------------------------------------------------ basics
    def execute(self, sql: str, params: tuple | list = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    def query(self, sql: str, params: tuple | list = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._conn.execute(sql, params).fetchall())

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---------------------------------------------------------------------- kv
    def kv_get(self, key: str, default: Any = None) -> Any:
        rows = self.query("SELECT value FROM kv WHERE key=?", (key,))
        if not rows:
            return default
        try:
            return json.loads(rows[0]["value"])
        except Exception:
            return default

    def kv_set(self, key: str, value: Any) -> None:
        self.execute(
            "INSERT INTO kv(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value)),
        )

    # ------------------------------------------------------------------ trades
    _TRADE_COLS = [
        "mode", "symbol", "side", "qty", "entry_price", "entry_time", "stop", "initial_stop",
        "take_profit", "exit_price", "exit_time", "pnl", "pnl_pct", "r_multiple", "fees",
        "status", "entry_reason", "exit_reason", "score", "strategies", "ai_comment", "meta",
    ]

    def _trade_values(self, t: Trade) -> list[Any]:
        d = asdict(t)
        d["strategies"] = json.dumps(d["strategies"])
        d["meta"] = json.dumps(d["meta"])
        return [d[c] for c in self._TRADE_COLS]

    def insert_trade(self, t: Trade) -> Trade:
        cols = ",".join(self._TRADE_COLS)
        marks = ",".join("?" for _ in self._TRADE_COLS)
        cur = self.execute(f"INSERT INTO trades({cols}) VALUES({marks})", self._trade_values(t))
        t.id = cur.lastrowid
        return t

    def update_trade(self, t: Trade) -> None:
        assert t.id is not None
        sets = ",".join(f"{c}=?" for c in self._TRADE_COLS)
        self.execute(f"UPDATE trades SET {sets} WHERE id=?", self._trade_values(t) + [t.id])

    @staticmethod
    def _row_to_trade(r: sqlite3.Row) -> Trade:
        d = dict(r)
        d["strategies"] = json.loads(d.get("strategies") or "{}")
        d["meta"] = json.loads(d.get("meta") or "{}")
        return Trade(**d)

    def open_trades(self, mode: str | None = None) -> list[Trade]:
        if mode:
            rows = self.query("SELECT * FROM trades WHERE status='open' AND mode=? ORDER BY id", (mode,))
        else:
            rows = self.query("SELECT * FROM trades WHERE status='open' ORDER BY id")
        return [self._row_to_trade(r) for r in rows]

    def closed_trades(self, mode: str | None = None, limit: int = 50, since: float | None = None) -> list[Trade]:
        sql = "SELECT * FROM trades WHERE status='closed'"
        params: list[Any] = []
        if mode:
            sql += " AND mode=?"
            params.append(mode)
        if since:
            sql += " AND exit_time>=?"
            params.append(since)
        sql += " ORDER BY exit_time DESC LIMIT ?"
        params.append(limit)
        return [self._row_to_trade(r) for r in self.query(sql, params)]

    def get_trade(self, trade_id: int) -> Trade | None:
        rows = self.query("SELECT * FROM trades WHERE id=?", (trade_id,))
        return self._row_to_trade(rows[0]) if rows else None

    def realized_pnl_since(self, mode: str, since: float) -> float:
        rows = self.query(
            "SELECT COALESCE(SUM(pnl),0) AS s FROM trades WHERE status='closed' AND mode=? AND exit_time>=?",
            (mode, since),
        )
        return float(rows[0]["s"])

    def trade_stats(self, mode: str | None = None) -> dict[str, Any]:
        trades = self.closed_trades(mode, limit=100_000)
        n = len(trades)
        if n == 0:
            return {"trades": 0, "win_rate": 0.0, "pnl": 0.0, "profit_factor": 0.0, "avg_r": 0.0}
        wins = [t for t in trades if (t.pnl or 0) > 0]
        gross_win = sum(t.pnl or 0 for t in wins)
        gross_loss = -sum(t.pnl or 0 for t in trades if (t.pnl or 0) < 0)
        return {
            "trades": n,
            "win_rate": len(wins) / n * 100,
            "pnl": sum(t.pnl or 0 for t in trades),
            "profit_factor": (gross_win / gross_loss) if gross_loss > 0 else float("inf") if gross_win > 0 else 0.0,
            "avg_r": sum(t.r_multiple or 0 for t in trades) / n,
        }

    # ------------------------------------------------------------------ equity
    def record_equity(self, mode: str, equity: float, cash: float) -> None:
        self.execute("INSERT INTO equity(ts, mode, equity, cash) VALUES(?,?,?,?)", (time.time(), mode, equity, cash))

    def equity_curve(self, mode: str, limit: int = 2000) -> list[dict[str, float]]:
        rows = self.query(
            "SELECT ts, equity, cash FROM equity WHERE mode=? ORDER BY ts DESC LIMIT ?", (mode, limit)
        )
        return [dict(r) for r in reversed(rows)]

    # ------------------------------------------------------------------ events
    def log_event(self, level: str, message: str, kind: str = "") -> None:
        self.execute(
            "INSERT INTO events(ts, level, kind, message) VALUES(?,?,?,?)", (time.time(), level, kind, message)
        )

    def recent_events(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.query("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]

    # -------------------------------------------------------------------- chat
    def add_chat(self, channel: str, role: str, content: str) -> None:
        self.execute(
            "INSERT INTO chat(ts, channel, role, content) VALUES(?,?,?,?)", (time.time(), channel, role, content)
        )

    def chat_history(self, channel: str, limit: int = 16) -> list[dict[str, str]]:
        rows = self.query(
            "SELECT role, content FROM chat WHERE channel=? ORDER BY id DESC LIMIT ?", (channel, limit)
        )
        return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]

    def clear_chat(self, channel: str) -> None:
        self.execute("DELETE FROM chat WHERE channel=?", (channel,))

    # ----------------------------------------------------------------- signals
    def add_signal(self, source: str, symbol: str, action: str, payload: dict[str, Any]) -> None:
        self.execute(
            "INSERT INTO signals(ts, source, symbol, action, payload) VALUES(?,?,?,?,?)",
            (time.time(), source, symbol, action, json.dumps(payload)),
        )

    def latest_signal(self, source: str, symbol: str, max_age_s: float) -> dict[str, Any] | None:
        rows = self.query(
            "SELECT * FROM signals WHERE source=? AND symbol=? AND ts>=? ORDER BY id DESC LIMIT 1",
            (source, symbol, time.time() - max_age_s),
        )
        if not rows:
            return None
        d = dict(rows[0])
        d["payload"] = json.loads(d.get("payload") or "{}")
        return d

    # ---------------------------------------------------------- strategy stats
    def save_strategy_stats(
        self, symbol: str, timeframe: str, strategy: str, params: dict, metrics: dict, weight: float
    ) -> None:
        self.execute(
            """INSERT INTO strategy_stats(symbol, timeframe, strategy, updated, params, metrics, weight)
               VALUES(?,?,?,?,?,?,?)
               ON CONFLICT(symbol, timeframe, strategy) DO UPDATE SET
               updated=excluded.updated, params=excluded.params, metrics=excluded.metrics, weight=excluded.weight""",
            (symbol, timeframe, strategy, time.time(), json.dumps(params), json.dumps(metrics), weight),
        )

    def strategy_stats(self, symbol: str, timeframe: str) -> dict[str, dict[str, Any]]:
        rows = self.query("SELECT * FROM strategy_stats WHERE symbol=? AND timeframe=?", (symbol, timeframe))
        out = {}
        for r in rows:
            out[r["strategy"]] = {
                "params": json.loads(r["params"] or "{}"),
                "metrics": json.loads(r["metrics"] or "{}"),
                "weight": r["weight"],
                "updated": r["updated"],
            }
        return out
