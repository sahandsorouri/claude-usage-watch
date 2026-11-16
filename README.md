# claude-usage-watch

A small Python script that watches your Claude plan usage meters (the 5-hour window and the weekly limit) and tells you when you are burning the week too fast. It runs every 5 minutes from a systemd user timer, keeps a short history, and sends:

- one alert when the weekly meter first passes 50, 75, 90 and 100 percent
- at most one pace alert a day, only when you are 10 or more points ahead of an even pace AND would run out 12 or more hours before the reset
- one alert when the 5-hour meter first passes 70 and 90 percent of the current window
- one short morning report (weekly use, use in the last 24 hours, pace, time to each reset)

It also writes the current numbers to a JSON file, so gauges and dashboards can show them. `macos/UsageGauges.swift` is a ready SwiftUI view for that.

Standard library only. Python 3.9 or newer.

## How it reads the meter

It calls the same endpoint the Claude Code `/usage` screen uses, with the OAuth access token Claude Code stores in `~/.claude/.credentials.json`. So it only works on a machine where you are logged in to Claude Code.

The token is **only read**. It is never printed, logged, copied or refreshed. Refreshing it from a second program could log your running Claude Code sessions out, so when the token has expired the script simply waits until a running Claude Code session renews it.

The endpoint is not documented and can change with any update. If it stops answering, the script sends one "cannot read" alert a day and nothing else.

## Setup

1. Copy `claude_usage_watch.py` somewhere, for example `~/claude-usage-watch/`.
2. Try it without touching anything:
   ```
   python3 claude_usage_watch.py --dry --daily --fake examples/fake-usage-response.json
   python3 claude_usage_watch.py --dry --daily
   ```
   The first uses a saved sample answer (no network). The second reads your real meter but writes nothing.
3. Create `~/.config/claude-usage-watch/env` from `.env.example` and fill in what you use.
4. Install the timer: copy `examples/claude-usage-watch.service` and `examples/claude-usage-watch.timer` to `~/.config/systemd/user/`, fix the script path in the service, then:
   ```
   systemctl --user daemon-reload
   systemctl --user enable --now claude-usage-watch.timer
   ```

Flags: `--dry` prints alerts and writes nothing, `--daily` sends the morning report now, `--fake FILE` reads the meter answer from a file.

## Environment variables

| Variable | Default | What it does |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | empty | Bot token for sending alerts with the Telegram Bot API |
| `TELEGRAM_CHAT_ID` | empty | The one chat that receives the alerts |
| `OUTBOX_DIR` | empty | If set, each alert is also written there as a text file, for another program to deliver |
| `JSON_OUTPUT` | empty | Path for the gauge JSON (comma separated for several copies) |
| `USAGE_TZ` | `UTC` | IANA time zone for the morning report and dates |
| `DAILY_HOUR` | `8` | Hour of the morning report in `USAGE_TZ` |
| `ALERT_LANG` | `en` | `en` for English, `fa` for Persian |
| `CLAUDE_CREDENTIALS` | `~/.claude/.credentials.json` | Where Claude Code keeps its token |
| `STATE_DIR` | `~/.local/state/claude-usage-watch` | History and "already sent" memory |

With neither Telegram nor `OUTBOX_DIR` set, alerts go to stdout (the systemd journal).

## Access

The bot here only sends. It never reads incoming messages and has no commands, so nobody can talk to it. It sends to exactly one chat, the one in `TELEGRAM_CHAT_ID`. Keep the env file readable only by you (`chmod 600`), since it holds the bot token.

## Gauge JSON

```json
{"week_pct": 55, "week_expected": 43, "week_resets": "...", "five_pct": 74, "five_resets": "...",
 "week_resets_ts": 1795209818, "five_resets_ts": 1794871418, "updated": "2026-11-16 21:23"}
```

`week_expected` is where an even pace over the 7 days would be right now.

## macOS gauges

`macos/UsageGauges.swift` is one self-contained SwiftUI file: two small half-circle gauges (weekly and 5-hour, the weekly one turns orange when you are 10 points ahead of pace) and an optional menu bar item.

1. Serve the JSON file over HTTP with any web server on your network.
2. Drop the file into your SwiftUI Mac app target and set `usageJSONURL` at the top.
3. Put `UsageGauges()` anywhere in a view. For the menu bar, call `UsageMenuBar.shared.start()` once at launch.
4. For a plain `http://` URL on another machine, allow it in the app's App Transport Security settings, and accept the Local Network prompt.

It fetches at most every 20 seconds and dims the gauges when the numbers are older than 15 minutes.

## Tests

```
python3 -m unittest discover -s tests -v
```

They cover the pace math and the alert thresholds, with no network.

Built by Sahand Sorouri, https://sahand.sorouri.com
