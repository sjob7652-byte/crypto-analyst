#!/bin/bash
# ============================================================
# Memecoin scanner bot — Oracle Cloud VM deployment script
# Run once inside OCI Cloud Shell (or any shell ON the VM):
#   bash ~/bot-deploy.sh
# Idempotent: safe to re-run any time.
# ============================================================
set -e
export DEBIAN_FRONTEND=noninteractive

BOT_DIR="$HOME/bot"
REPO_DIR="$BOT_DIR/crypto-analyst"
VENV="$BOT_DIR/venv"
REPO_URL="https://github.com/sjob7652-byte/crypto-analyst"

echo "=== [1/6] system packages ==="
sudo apt-get update -qq
sudo apt-get install -y -qq python3 python3-venv git
command -v flock >/dev/null || sudo apt-get install -y -qq util-linux

echo "=== [2/6] repo ==="
mkdir -p "$BOT_DIR"
if [ ! -d "$REPO_DIR/.git" ]; then
  git clone -q "$REPO_URL" "$REPO_DIR"
else
  echo "repo already present"
fi

echo "=== [3/6] python venv + deps ==="
[ -d "$VENV" ] || python3 -m venv "$VENV"
"$VENV/bin/pip" install -q --upgrade pip
"$VENV/bin/pip" install -q -r "$REPO_DIR/requirements.txt"
"$VENV/bin/python" -m py_compile "$REPO_DIR/main.py" "$REPO_DIR/postmarket.py" && echo "compile OK"

echo "=== [4/6] runner wrappers ==="
cat > "$BOT_DIR/run_scan.sh" << 'EOF'
#!/bin/bash
# 2-minute scanner: self-update code, load secrets, run once (flock in cron prevents overlap)
REPO_DIR="$HOME/bot/crypto-analyst"
cd "$REPO_DIR"
git fetch -q origin >/dev/null 2>&1 && git reset -q --hard origin/main >/dev/null 2>&1 || true
if [ -f "$HOME/bot/.env" ]; then set -a; . "$HOME/bot/.env"; set +a; fi
exec "$HOME/bot/venv/bin/python" main.py
EOF
cat > "$BOT_DIR/run_monitor.sh" << 'EOF'
#!/bin/bash
# Lightweight position monitor (every minute): self-update code, load secrets,
# then main.py --monitor — open positions only (prices + SL/TP/expiry exits).
# No discovery, no buys, no digests. Own flock lock so a slow scan never
# blocks it; state.lock serializes actual state mutations with the scanner.
REPO_DIR="$HOME/bot/crypto-analyst"
cd "$REPO_DIR"
git fetch -q origin >/dev/null 2>&1 && git reset -q --hard origin/main >/dev/null 2>&1 || true
if [ -f "$HOME/bot/.env" ]; then set -a; . "$HOME/bot/.env"; set +a; fi
exec "$HOME/bot/venv/bin/python" main.py --monitor
EOF
cat > "$BOT_DIR/run_postmarket.sh" << 'EOF'
#!/bin/bash
# daily post-market report 22:59 UTC
REPO_DIR="$HOME/bot/crypto-analyst"
cd "$REPO_DIR"
git fetch -q origin >/dev/null 2>&1 && git reset -q --hard origin/main >/dev/null 2>&1 || true
if [ -f "$HOME/bot/.env" ]; then set -a; . "$HOME/bot/.env"; set +a; fi
exec "$HOME/bot/venv/bin/python" postmarket.py
EOF
chmod +x "$BOT_DIR/run_scan.sh" "$BOT_DIR/run_postmarket.sh" "$BOT_DIR/run_monitor.sh"
chmod +x "$REPO_DIR/run_research.sh" 2>/dev/null || true

echo "=== [5/6] cron ==="
CRON_SCAN="*/2 * * * * /usr/bin/flock -n $BOT_DIR/scan.lock $BOT_DIR/run_scan.sh >> $BOT_DIR/scan.log 2>&1"
CRON_PM="59 22 * * * /usr/bin/flock -n $BOT_DIR/scan.lock $BOT_DIR/run_postmarket.sh >> $BOT_DIR/postmarket.log 2>&1"
CRON_MON="* * * * * /usr/bin/flock -n $BOT_DIR/monitor.lock $BOT_DIR/run_monitor.sh >> $BOT_DIR/monitor.log 2>&1"
# الأبحاث الليلية (2026-09-25): ثقيلة عمداً — 02:17 بتوقيت UTC
CRON_RESEARCH="17 2 * * * /usr/bin/flock -n $BOT_DIR/research.lock $REPO_DIR/run_research.sh >> $BOT_DIR/research.log 2>&1"
CRON_HEAVY="* * * * * /usr/bin/flock -n $BOT_DIR/heavy.lock /bin/bash $REPO_DIR/supervise.sh >> $BOT_DIR/heavy.log 2>&1"
( crontab -l 2>/dev/null | grep -v "bot/run_scan.sh\|bot/run_postmarket.sh\|bot/run_monitor.sh\|run_research.sh\|supervise.sh"; echo "$CRON_SCAN"; echo "$CRON_PM"; echo "$CRON_MON"; echo "$CRON_RESEARCH"; echo "$CRON_HEAVY" ) | crontab -
crontab -l | grep "bot/run_\|run_research\|supervise"

echo "=== [6/6] secrets check ==="
if [ -f "$BOT_DIR/.env" ]; then
  echo ".env found — checking keys (values hidden):"
  for k in TELEGRAM_BOT_TOKEN TELEGRAM_CHAT_ID GH_PAT GIST_ID; do
    if grep -q "^$k=.\+" "$BOT_DIR/.env"; then echo "  $k: set"; else echo "  $k: MISSING"; fi
  done
else
  echo "WARNING: $BOT_DIR/.env not found — create it now:"
  echo "  cat > $BOT_DIR/.env << 'EOF'"
  echo "  TELEGRAM_BOT_TOKEN=..."
  echo "  TELEGRAM_CHAT_ID=..."
  echo "  GH_PAT=..."
  echo "  GIST_ID=7181de8f94858379915d85d0a04bcbef"
  echo "  EOF"
  echo "  chmod 600 $BOT_DIR/.env"
fi

echo ""
echo "DEPLOY DONE. Scanner runs every 5 min, post-market daily 22:59 UTC."
echo "Logs: $BOT_DIR/scan.log  |  $BOT_DIR/postmarket.log"
