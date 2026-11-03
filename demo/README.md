# Account Radar — Live Demo Mode

Demo Mode is a presentation-friendly way to launch **the same Account Radar
product** for a management demonstration. It is **live**: every
*Analyze account* runs fresh research against current public-domain sources at
that moment. It is not a frozen or replayed demo.

## How to start

| Purpose | Command (from the project folder) | Address |
|---|---|---|
| Normal development | `py -m streamlit run app.py` | http://localhost:8501 |
| Management demo | `.\run_demo.bat` (or double-click it) | http://localhost:8502 |

`run_demo.bat` changes to the project folder, uses the Windows Python launcher
(`%LOCALAPPDATA%\Programs\Python\Launcher\py.exe`, falling back to `py`), starts
Streamlit on port **8502** and opens the browser. Both can run at the same time.

## What Demo Mode is

```
                    ┌── py -m streamlit run app.py            (development)
Account Radar core ─┤
                    └── run_demo.bat → demo/demo_app.py      (live demo)
```

* **One engine.** `demo/demo_app.py` is a few lines: it calls `app.run_demo()`,
  which runs the normal `app.main()` with a small presentation layer
  (`ui/demo_mode.py`). Research providers, the acquisition pipeline, identity
  and official-domain verification, evidence logic, capability mapping,
  opportunity qualification, WATCH, UNDERSTAND, DECIDE, PEOPLE and ACT are the
  exact same code as normal use. There is no second copy of any of them.
* **Live research.** Results are current at the time of the run. They can
  legitimately differ from an earlier run of the same account (for example
  VALIDATE yesterday, MONITOR today) because the public evidence changed.
  Nothing forces the demo to reproduce historical numbers.
* **Oman Airports is only the example.** The sidebar is pre-filled with
  *Oman Airports Management Company / Oman*. Replace it with any organization
  and country (e.g. Ooredoo / Oman, Omantel / Oman, Vodafone Oman / Oman).
  The research pipeline is organization-agnostic.
* **No research on start.** Opening Demo Mode does nothing expensive. Research
  starts only when you select **Analyze account**.

## What Demo Mode adds (presentation only)

* A small **LIVE DEMO MODE** banner.
* A research progress panel while the live run executes.
* After the run, a **LIVE RESEARCH** strip: the runtime *Analyzed* timestamp
  (Gulf Standard Time), qualified sources, account-specific developments,
  opportunities, and the DECIDE state with its confidence — all taken from
  the current run.
* Browser error details limited to generic messages and the developer toolbar
  hidden (`--client.showErrorDetails none`, `--client.toolbarMode viewer`).

## Fresh research and caching

Account Radar has **no research-result cache**: every *Analyze account*, in
either mode, calls the research providers again. (The only in-process memo is
a per-URL publication date, which does not change the evidence.)

Normal mode re-uses an official website already *verified earlier in the same
browser session* for that organization, for identity stability. Demo Mode runs
**fully fresh** and re-verifies identity on every run. Before each run Demo
Mode also clears the previous account result from the screen.

## When live research fails

Demo Mode never falls back to an older result. If the live run fails — or
completes without retrieving anything from the providers — the screen shows:

> Live research could not be completed. No current account result is being displayed.

with the likely cause (API key not configured or rejected, provider usage
limit, timeout, network problem, no results) and a **Retry research** button.
No stack trace, raw provider response or API key is shown. A run that does
retrieve sources but finds little evidence is a real current result and is
shown as such.

## Requirements

The same as normal live research. Search providers are used in this order,
and only those with a key configured take part:

| Order | Provider | Environment variable | Role |
|---|---|---|---|
| 1 | Brave Search API (Search endpoint) | `BRAVE_SEARCH_API_KEY` | Primary |
| 2 | Tavily | `TAVILY_API_KEY` | Secondary |
| 3 | Serper | `SERPER_API_KEY` | Last fallback |

At least one search key is required. A fallback provider is called only when
the provider before it is unavailable, errors, or returns genuinely sparse
coverage for a whole query family -- not for every query -- so normal runs use
Brave alone. Each research run records `providers_used` and
`provider_requests` (queries sent per provider) in its acquisition
diagnostics.

Costs are governed by each provider's own plan terms, not by Account Radar.
Brave's monthly free allowance and per-request pricing are subject to Brave's
current pricing and terms; Tavily's free plan has a monthly credit allowance;
Serper is billed per its own plan. Account Radar does not purchase credits or
change any billing setting, and there is no unlimited free search. If a
provider's allowance is exhausted, its requests fail and the next provider in
the chain is used. Consider setting a usage/spending limit in each provider's
dashboard.

`OPENROUTER_API_KEY` is optional and enables only the optional AI
interpretation. DECIDE and ACT do not depend on it. (OpenRouter's search
provider is not part of the search chain.)

`run_demo.bat` warns if no search key is set; it never prints a key.

## Before presenting

1. Double-click `run_demo.bat`; the browser opens on port 8502.
2. Run one analysis of the account you will show, to confirm keys and network.
3. During the demo, select **Analyze account** and talk through the progress
   panel while research runs (it can take a little while).
4. Present the result: WATCH → UNDERSTAND → DECIDE → PEOPLE → ACT.
