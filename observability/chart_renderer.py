from __future__ import annotations

import html
import os

import pandas as pd

try:
    import plotly.graph_objects as go
except ModuleNotFoundError:
    go = None


DEFAULT_OUTPUT_PATH = "reports/backtest_dashboard.html"


def _events_frame(events):
    if not events:
        return pd.DataFrame()
    event_df = pd.DataFrame(events)
    if "timestamp" in event_df.columns:
        event_df["timestamp"] = pd.to_datetime(event_df["timestamp"], utc=True, errors="coerce")
    return event_df


def _signal_hover_text(event_df: pd.DataFrame) -> list[str]:
    hover_text = []
    for _, row in event_df.iterrows():
        hover_text.append(
            "<br>".join(
                [
                    f"bar_index: {row.get('bar_index')}",
                    f"ema44_at_signal: {row.get('ema44_at_signal')}",
                    f"ema144_4h_at_signal: {row.get('ema144_4h_at_signal')}",
                    f"atr_at_signal: {row.get('atr_at_signal')}",
                    f"macd_line_at_signal: {row.get('macd_line_at_signal')}",
                    f"macd_signal_at_signal: {row.get('macd_signal_at_signal')}",
                    f"macd_hist_at_signal: {row.get('macd_hist_at_signal')}",
                    f"rsi_at_signal: {row.get('rsi_at_signal')}",
                    f"trend_status: {row.get('trend_status')}",
                    f"reason_code: {row.get('reason_code')}",
                    f"signal_rejected_reason: {row.get('signal_rejected_reason')}",
                    f"cooldown_remaining: {row.get('cooldown_remaining')}",
                    f"swing_structure_state: {row.get('swing_structure_state')}",
                ]
            )
        )
    return hover_text


def _exit_hover_text(event_df: pd.DataFrame) -> list[str]:
    hover_text = []
    for _, row in event_df.iterrows():
        hover_text.append(
            "<br>".join(
                [
                    f"bar_index: {row.get('bar_index')}",
                    f"exit_action: {row.get('exit_action')}",
                    f"exit_type: {row.get('exit_type')}",
                    f"reason_code: {row.get('reason_code')}",
                    f"sell_pct: {row.get('sell_pct')}",
                    f"holding_bars: {row.get('holding_bars')}",
                    f"mfe: {row.get('mfe')}",
                    f"current_return: {row.get('current_return')}",
                    f"pnl: {row.get('pnl')}",
                ]
            )
        )
    return hover_text


def _fallback_chart_html(df: pd.DataFrame, events) -> str:
    event_df = _events_frame(events)
    signal_count = len(event_df[event_df["event_type"].isin(["signal_trigger", "rejected_signal"])]) if not event_df.empty else 0
    entry_count = len(event_df[event_df["event_type"] == "entry"]) if not event_df.empty else 0
    exit_count = len(event_df[event_df["event_type"] == "exit"]) if not event_df.empty else 0

    latest_rows = df.tail(20)[["timestamp", "open", "high", "low", "close", "ema44", "atr"]].copy()
    latest_rows["timestamp"] = latest_rows["timestamp"].astype(str)
    table_rows = []
    for _, row in latest_rows.iterrows():
        cells = "".join(
            f"<td>{html.escape(str(row[column]))}</td>"
            for column in ["timestamp", "open", "high", "low", "close", "ema44", "atr"]
        )
        table_rows.append(f"<tr>{cells}</tr>")

    body = "".join(table_rows) if table_rows else "<tr><td colspan='7'>No data</td></tr>"
    return f"""
    <div style="padding:16px;border:1px solid #e5e7eb;border-radius:12px;background:#fff7ed;">
      <p style="margin-top:0;"><strong>Plotly is not installed.</strong> Interactive chart rendering is unavailable in this environment.</p>
      <p>Signal events: {signal_count} | Entries: {entry_count} | Exits: {exit_count}</p>
      <div style="overflow-x:auto;">
        <table style="width:100%;border-collapse:collapse;font-size:14px;">
          <thead>
            <tr>
              <th>timestamp</th><th>open</th><th>high</th><th>low</th><th>close</th><th>ema44</th><th>atr</th>
            </tr>
          </thead>
          <tbody>{body}</tbody>
        </table>
      </div>
    </div>
    """


def build_chart_figure(df: pd.DataFrame, events):
    if go is None:
        raise ModuleNotFoundError("No module named 'plotly'")

    fig = go.Figure()
    fig.add_trace(
        go.Candlestick(
            x=df["timestamp"],
            open=df["open"],
            high=df["high"],
            low=df["low"],
            close=df["close"],
            name="Candlestick",
        )
    )

    if "ema44" in df.columns:
        fig.add_trace(
            go.Scatter(
                x=df["timestamp"],
                y=df["ema44"],
                mode="lines",
                name="EMA44",
                line={"color": "#1f77b4", "width": 1.5},
            )
        )

    swing_high_df = df[df["swing_high"].notna()] if "swing_high" in df.columns else pd.DataFrame()
    swing_low_df = df[df["swing_low"].notna()] if "swing_low" in df.columns else pd.DataFrame()

    if not swing_high_df.empty:
        fig.add_trace(
            go.Scatter(
                x=swing_high_df["timestamp"],
                y=swing_high_df["swing_high"],
                mode="markers",
                name="Swing High",
                marker={"color": "#d62728", "size": 8, "symbol": "triangle-down"},
            )
        )

    if not swing_low_df.empty:
        fig.add_trace(
            go.Scatter(
                x=swing_low_df["timestamp"],
                y=swing_low_df["swing_low"],
                mode="markers",
                name="Swing Low",
                marker={"color": "#2ca02c", "size": 8, "symbol": "triangle-up"},
            )
        )

    event_df = _events_frame(events)
    if not event_df.empty:
        signal_df = event_df[event_df["event_type"].isin(["signal_trigger", "rejected_signal"])]
        entry_df = event_df[event_df["event_type"] == "entry"]
        exit_df = event_df[event_df["event_type"] == "exit"]

        if not signal_df.empty:
            fig.add_trace(
                go.Scatter(
                    x=signal_df["timestamp"],
                    y=signal_df["price"],
                    mode="markers",
                    name="Signals",
                    marker={"color": "#ff7f0e", "size": 10, "symbol": "diamond"},
                    text=_signal_hover_text(signal_df),
                    hovertemplate="%{text}<extra></extra>",
                )
            )

        if not entry_df.empty:
            fig.add_trace(
                go.Scatter(
                    x=entry_df["timestamp"],
                    y=entry_df["price"],
                    mode="markers",
                    name="Entries",
                    marker={"color": "#17becf", "size": 11, "symbol": "triangle-up"},
                    hovertemplate="entry<br>bar_index=%{customdata}<extra></extra>",
                    customdata=entry_df["bar_index"],
                )
            )

        if not exit_df.empty:
            fig.add_trace(
                go.Scatter(
                    x=exit_df["timestamp"],
                    y=exit_df["price"],
                    mode="markers",
                    name="Exits",
                    marker={"color": "#9467bd", "size": 11, "symbol": "x"},
                    text=_exit_hover_text(exit_df),
                    hovertemplate="%{text}<extra></extra>",
                )
            )

    fig.update_layout(
        title="Backtest Dashboard",
        xaxis_title="Timestamp",
        yaxis_title="Price",
        xaxis_rangeslider_visible=False,
        template="plotly_white",
        legend={"orientation": "h"},
        hovermode="x unified",
    )
    return fig


def build_chart_html(df: pd.DataFrame, events) -> str:
    if go is None:
        return _fallback_chart_html(df, events)
    figure = build_chart_figure(df, events)
    return figure.to_html(full_html=False, include_plotlyjs="cdn")


def render_chart(df: pd.DataFrame, events, output_path: str = DEFAULT_OUTPUT_PATH) -> str:
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    html = build_chart_html(df, events)
    with open(output_path, "w", encoding="utf-8") as file:
        file.write(html)
    return output_path
