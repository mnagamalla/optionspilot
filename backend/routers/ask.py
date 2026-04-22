import yfinance as yf
import json
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from anthropic import Anthropic

from backend.db.database import get_db
from backend.db import journal_repo as repo

router = APIRouter(prefix="/ask", tags=["Ask MyTradingBot"])
client = Anthropic()


class AskRequest(BaseModel):
    question: str
    account_id: Optional[int] = None


class AskResponse(BaseModel):
    answer:     str
    follow_ups: list[str]


def build_context(db: Session, account_id: Optional[int]) -> str:
    """Pull live journal data to inject into the system prompt."""

    # P&L summary
    pnl = repo.get_pnl_summary(db, account_id)

    # Recent trades (last 15)
    trades = repo.get_trades(db, account_id)[:15]

    # Open lots with cost basis
    lots = repo.get_lots_with_basis(db, account_id)

    # Open wheel cycles
    cycles = repo.get_wheel_cycles(db, account_id)

    # Win rate
    wr = repo.get_win_rate(db, account_id)

    # Fetch current prices for lots
    tickers = list({l["ticker"] for l in lots if l["ticker"]})
    prices  = {}
    if tickers:
        try:
            data = yf.download(tickers, period="1d",
                               progress=False, group_by="ticker")
            for t in tickers:
                try:
                    prices[t] = float(data["Close"].iloc[-1]) \
                                 if len(tickers) == 1 \
                                 else float(data[t]["Close"].iloc[-1])
                except Exception:
                    pass
        except Exception:
            pass

    # Update lots with live prices
    for lot in lots:
        lot["current_price"] = prices.get(lot["ticker"])
        if lot["current_price"] and lot["adj_basis"]:
            lot["buffer_pct"] = round(
                (lot["current_price"] - lot["adj_basis"])
                / lot["adj_basis"] * 100, 2
            )

    context = f"""
## My Trading Journal Context

### P&L Summary
- All Time:   Options ${pnl['all_time']['options']:,.2f} | Stock ${pnl['all_time']['stock']:,.2f} | Dividends ${pnl['all_time']['dividend']:,.2f} | Net ${pnl['all_time']['total']:,.2f}
- YTD:        Options ${pnl['ytd']['options']:,.2f} | Stock ${pnl['ytd']['stock']:,.2f} | Dividends ${pnl['ytd']['dividend']:,.2f} | Net ${pnl['ytd']['total']:,.2f}
- This Month: Options ${pnl['this_month']['options']:,.2f} | Net ${pnl['this_month']['total']:,.2f}
- This Week:  Options ${pnl['this_week']['options']:,.2f} | Net ${pnl['this_week']['total']:,.2f}

### Win Rate
- Total contracts: {wr['total']} | Win rate: {wr['win_rate_pct']}%
- Expired worthless: {wr['expired_worthless']} | Closed profit: {wr['closed_profit']} | Closed loss: {wr['closed_loss']}

### Stock Lots (with Adjusted Cost Basis)
{json.dumps([{
    'ticker':        l['ticker'],
    'lot':           l['lot_number'],
    'shares':        l['shares'],
    'avg_cost':      l['avg_cost'],
    'cc_premiums':   l['cc_premiums'],
    'adj_basis':     l['adj_basis'],
    'current_price': l.get('current_price'),
    'buffer_pct':    l.get('buffer_pct'),
    'alert':         l['alert'],
} for l in lots], indent=2)}

### Open Wheel Cycles
{json.dumps([{
    'ticker':        c['ticker'],
    'status':        c['status'],
    'total_premium': c['total_premium'],
    'opened_at':     c['opened_at'],
} for c in cycles if c['status'] not in ('closed','called_away')], indent=2)}

### Recent Trades (last 15)
{json.dumps([{
    'date':         t['opened_at'][:10] if t['opened_at'] else None,
    'ticker':       t['ticker'],
    'type':         t['trade_type'],
    'strike':       t['strike'],
    'expiry':       t['expiry'],
    'premium':      t['premium'],
    'total':        t['total_amount'],
    'status':       t['status'],
} for t in trades], indent=2)}
"""
    return context

@router.post("/", response_model=AskResponse)
def ask(req: AskRequest, db: Session = Depends(get_db)):
    if not req.question.strip():
        raise HTTPException(status_code=422, detail="Question cannot be empty")

    # Check API key upfront — give a clear error instead of a cryptic 500
    import os
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise HTTPException(
            status_code=503,
            detail="Anthropic API key not configured. Add ANTHROPIC_API_KEY to your .env file and restart the server."
        )

    context = build_context(db, req.account_id)

    system_prompt = f"""You are MyTradingBot, a personal options trading assistant.
You help with wheel strategy (CSPs and covered calls), spread analysis,
position management, and P&L interpretation.

You have direct access to the user's live trading journal shown below.
Always reference their actual positions, P&L, and trade history when relevant.
Be concise, specific, and actionable. Avoid generic advice — ground every
answer in their actual data.

At the end of every response, suggest exactly 3 follow-up questions the user
might want to ask next. Format them as a JSON block at the very end like this:
{{"follow_ups": ["question 1", "question 2", "question 3"]}}

{context}"""

    try:
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            messages=[{"role": "user", "content": req.question}],
            system=system_prompt,
        )
        raw = message.content[0].text

        answer     = raw
        follow_ups = []
        if '{"follow_ups"' in raw:
            parts      = raw.split('{"follow_ups"')
            answer     = parts[0].strip()
            try:
                fu_json    = json.loads('{"follow_ups"' + parts[1])
                follow_ups = fu_json.get("follow_ups", [])
            except Exception:
                pass

        return AskResponse(answer=answer, follow_ups=follow_ups)

    except HTTPException:
        raise

    except Exception as e:
        err = str(e)
        if "api_key" in err.lower() or "authentication" in err.lower():
            detail = "Invalid Anthropic API key. Check ANTHROPIC_API_KEY in your .env file."
        elif "rate_limit" in err.lower():
            detail = "Anthropic rate limit hit. Wait a moment and try again."
        elif "overloaded" in err.lower():
            detail = "Anthropic servers are busy. Try again in a few seconds."
        else:
            detail = f"AI error: {err}"
        raise HTTPException(status_code=503, detail=detail)