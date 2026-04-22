from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship
from datetime import datetime

from backend.db.database import Base


class Account(Base):
    __tablename__ = "accounts"

    id                = Column(Integer, primary_key=True, index=True)
    name              = Column(String, nullable=False)
    rh_account_number = Column(String, unique=True, nullable=False)
    account_type      = Column(String, default="individual")
    created_at        = Column(DateTime, default=datetime.utcnow)
    last_synced_at    = Column(DateTime, nullable=True)

    trades = relationship("Trade",      back_populates="account")
    lots   = relationship("StockLot",   back_populates="account")
    cycles = relationship("WheelCycle", back_populates="account")


class StockLot(Base):
    """One lot = one 100-share block (or partial). Each tracked independently."""
    __tablename__ = "stock_lots"

    id            = Column(Integer, primary_key=True, index=True)
    account_id    = Column(Integer, ForeignKey("accounts.id"), nullable=False)
    ticker        = Column(String, nullable=False)
    lot_number    = Column(Integer, default=1)
    shares        = Column(Float, nullable=False)
    avg_cost      = Column(Float, nullable=False)
    purchase_date = Column(DateTime, nullable=True)
    created_at    = Column(DateTime, default=datetime.utcnow)

    account  = relationship("Account",    back_populates="lots")
    premiums = relationship("LotPremium", back_populates="lot")
    trades   = relationship("Trade",      back_populates="lot")


class LotPremium(Base):
    """Records each CC premium collected against a specific lot."""
    __tablename__ = "lot_premiums"

    id                = Column(Integer, primary_key=True, index=True)
    lot_id            = Column(Integer, ForeignKey("stock_lots.id"), nullable=False)
    trade_id          = Column(Integer, ForeignKey("trades.id"),     nullable=True)
    premium_per_share = Column(Float, nullable=False)
    collected_date    = Column(DateTime, default=datetime.utcnow)

    lot   = relationship("StockLot", back_populates="premiums")
    trade = relationship("Trade",    back_populates="lot_premium")


class Trade(Base):
    __tablename__ = "trades"

    id           = Column(Integer, primary_key=True, index=True)
    account_id   = Column(Integer, ForeignKey("accounts.id"), nullable=False)
    rh_order_id  = Column(String, unique=True, nullable=True)
    ticker       = Column(String, nullable=False)

    # short_put | long_put | short_call | long_call
    # stock | dividend | iron_condor | debit_spread | credit_spread
    trade_type   = Column(String, nullable=False)
    option_type  = Column(String, nullable=True)
    side         = Column(String, nullable=True)

    strike       = Column(Float,   nullable=True)
    expiry       = Column(String,  nullable=True)
    quantity     = Column(Float,   default=1)
    premium      = Column(Float,   nullable=True)
    total_amount = Column(Float,   nullable=True)
    status       = Column(String,  default="open")
    # open | closed | expired | assigned | called_away

    lot_id       = Column(Integer, ForeignKey("stock_lots.id"), nullable=True)
    group_id     = Column(String,  nullable=True)
    pnl          = Column(Float,   nullable=True)
    opened_at    = Column(DateTime, nullable=True)
    closed_at    = Column(DateTime, nullable=True)
    created_at   = Column(DateTime, default=datetime.utcnow)
    notes        = Column(Text,    nullable=True)

    account     = relationship("Account",       back_populates="trades")
    lot         = relationship("StockLot",      back_populates="trades")
    lot_premium = relationship("LotPremium",    back_populates="trade", uselist=False)
    cycle_legs  = relationship("WheelCycleLeg", back_populates="trade")


class WheelCycle(Base):
    __tablename__ = "wheel_cycles"

    id            = Column(Integer, primary_key=True, index=True)
    account_id    = Column(Integer, ForeignKey("accounts.id"),   nullable=False)
    ticker        = Column(String,  nullable=False)
    lot_id        = Column(Integer, ForeignKey("stock_lots.id"), nullable=True)
    status        = Column(String,  default="open")
    # open | assigned | covered | closed | called_away
    total_premium = Column(Float,   default=0.0)
    opened_at     = Column(DateTime, nullable=True)
    closed_at     = Column(DateTime, nullable=True)
    net_pnl       = Column(Float,   nullable=True)
    created_at    = Column(DateTime, default=datetime.utcnow)

    account = relationship("Account",       back_populates="cycles")
    legs    = relationship("WheelCycleLeg", back_populates="cycle")


class WheelCycleLeg(Base):
    __tablename__ = "wheel_cycle_legs"

    id       = Column(Integer, primary_key=True, index=True)
    cycle_id = Column(Integer, ForeignKey("wheel_cycles.id"), nullable=False)
    trade_id = Column(Integer, ForeignKey("trades.id"),       nullable=False)
    leg_type = Column(String)    # csp | assignment | cc | exit
    sequence = Column(Integer, default=1)

    cycle = relationship("WheelCycle", back_populates="legs")
    trade = relationship("Trade",      back_populates="cycle_legs")
