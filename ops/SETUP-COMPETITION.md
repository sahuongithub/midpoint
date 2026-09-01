# Starting the judged account

The agent has been proving itself on a research account. The account the contest
is judged on has never traded, and it needs a record with some length by the
deadline. This is the only step that needs you rather than the machine, because
it needs your Alpaca login.

It takes about two minutes.

## 1. Get the API keys

1. Sign in at **alpaca.markets** and switch to **Paper Trading** (top-left toggle).
2. Make sure the account shown is the one on the submission: **PA32CGA2U1DY**.
   If you have several paper accounts, pick that one before going further.
3. In the right-hand panel find **API Keys**, and click **Generate** (or
   **Regenerate**).
4. Alpaca shows the **secret key once and never again.** Copy both values now.

## 2. Save them

Paste this into a terminal, replacing the two values:

    mkdir -p ~/.config/midpoint
    cat > ~/.config/midpoint/competition.env <<'ENV'
    ALPACA_API_KEY=PUT_THE_KEY_HERE
    ALPACA_SECRET_KEY=PUT_THE_SECRET_HERE
    ENV
    chmod 600 ~/.config/midpoint/competition.env

The `chmod` matters: it makes the file readable only by you. The file is
gitignored and never leaves the machine.

## 3. Check it before trusting it

    cd ~/midpoint
    set -a && source ~/.config/midpoint/competition.env && set +a
    python3 tools/preflight.py --expect PA32CGA2U1DY

Seventeen checks. The last line must read **clear to start**. If it names a
different account number, the keys belong to the wrong paper account -- go back
to step 1 and switch accounts before generating.

## 4. Run the judged sessions

    ./ops/start_day.sh --competition

From then on that is the command for the judged record, and the plain
`./ops/start_day.sh` stays with the research account.

## Why two accounts

Research probes deliberately buy and sell to measure how the venue behaves. One
of them left $294 of paper profit on the research account in a few seconds --
real money in the account, and not a trading result by any reading. An account
carrying that traffic has an equity curve nobody can interpret, and separating
them afterwards is arithmetic nobody should have to trust.

So the judged account only ever sees the agent, and the risk kernel is told which
account this session may touch. Point it at anything else and every order is
refused at the first gate, whatever the account number is -- checked against ten
thousand randomly generated cases.
