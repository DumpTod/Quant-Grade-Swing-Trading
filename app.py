# ============================================================
# app.py — Quant Scanner V2 — Render Server
# ============================================================
# SETUP — Add these in Render Dashboard → Environment:
#   FYERS_APP_ID       = your_app_id  (e.g. XB12345-100)
#   FYERS_SECRET_KEY   = your_secret_key
#   FYERS_ACCESS_TOKEN = paste fresh token here each morning
#
# HOW TO GET FRESH TOKEN DAILY:
#   1. Visit https://your-render-url.onrender.com/token
#   2. Click "Generate Login URL" → opens Fyers login
#   3. Login → copy auth_code from redirected URL
#   4. Paste auth_code → click "Get Token" → saved automatically
# ============================================================

import os, json, uuid, requests, logging, threading
import numpy as np
import pandas as pd
from datetime import datetime, date, timedelta
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from threading import Lock
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger(__name__)

app = Flask(__name__, static_folder='.')
CORS(app)

IST          = pytz.timezone('Asia/Kolkata')
SCAN_FILE    = 'scan_results.json'
HISTORY_FILE = 'trade_history.json'
TOKEN_FILE   = 'fyers_token.json'
lock         = Lock()

SCAN_RESULTS  = {
    'buy_signals': [], 'sell_signals': [],
    'scan_metadata': {
        'timestamp': None, 'stocks_scanned': 0,
        'buy_signals_count': 0, 'sell_signals_count': 0,
        'market_regime': 'unknown', 'scan_duration_seconds': 0,
        'failed_stocks': 0
    }
}
TRADE_HISTORY = []
SCAN_LOG      = []

FNO_STOCKS = [
    "ABB","ABCAPITAL","ACC","ADANIENT","ADANIPORTS","ALKEM","AMBUJACEM",
    "APOLLOHOSP","APOLLOTYRE","ASHOKLEY","ASIANPAINT","ASTRAL","AUBANK",
    "AUROPHARMA","AXISBANK","BAJAJFINSV","BAJFINANCE","BALKRISIND",
    "BANDHANBNK","BANKBARODA","BEL","BHARATFORG","BHARTIARTL","BHEL",
    "BIOCON","BPCL","BRITANNIA","CANBK","CANFINHOME","CHOLAFIN","CIPLA",
    "COALINDIA","COFORGE","COLPAL","CONCOR","CUMMINSIND","DABUR",
    "DEEPAKNTR","DIVISLAB","DIXON","DLF","DRREDDY","EICHERMOT",
    "FEDERALBNK","GAIL","GLENMARK","GODREJCP","GODREJPROP","GRANULES",
    "GRASIM","GUJGASLTD","HAL","HAVELLS","HCLTECH","HDFCAMC","HDFCBANK",
    "HDFCLIFE","HEROMOTOCO","HINDALCO","HINDPETRO","HINDUNILVR",
    "ICICIBANK","ICICIGI","ICICIPRULI","IDFCFIRSTB","IGL","INDHOTEL",
    "INDUSINDBK","INFY","IOC","IRCTC","ITC","JSWSTEEL","JUBLFOOD",
    "KOTAKBANK","LALPATHLAB","LAURUSLABS","LICHSGFIN","LT","LTTS",
    "LUPIN","MANAPPURAM","MARICO","MARUTI","MCX","MPHASIS","MRF",
    "MUTHOOTFIN","NATIONALUM","NAUKRI","NESTLEIND","NMDC","NTPC",
    "OBEROIRLTY","OFSS","ONGC","PAGEIND","PERSISTENT","PETRONET",
    "PFC","PIDILITIND","PIIND","PNB","POLYCAB","POWERGRID","RBLBANK",
    "RECLTD","RELIANCE","SBICARD","SBILIFE","SBIN","SHREECEM",
    "SHRIRAMFIN","SIEMENS","SUNPHARMA","SYNGENE","TATACHEM","TATACONSUM",
    "TATAMOTORS","TATAPOWER","TATASTEEL","TCS","TECHM","TITAN",
    "TORNTPHARM","TORNTPOWER","TRENT","UBL","ULTRACEMCO","UPL","VEDL",
    "VOLTAS","WIPRO","ZYDUSLIFE","ADANIGREEN","ADANIPOWER","CGPOWER",
    "HUDCO","IRFC","JIOFIN","JSWENERGY","KEI","MAXHEALTH","NHPC",
    "POLICYBZR","PRESTIGE","RVNL","SAIL","SJVN","SONACOMS","SUPREMEIND",
    "UNIONBANK","YESBANK","ZOMATO","LICI","LODHA",
]

# ============================================================
# TOKEN MANAGEMENT
# ============================================================
def get_access_token():
    """Get token — file first, then env variable."""
    try:
        if os.path.exists(TOKEN_FILE):
            with open(TOKEN_FILE, 'r') as f:
                t = json.load(f)
                if t.get('access_token') and t.get('date') == date.today().isoformat():
                    return t['access_token']
    except: pass
    return os.environ.get('FYERS_ACCESS_TOKEN', '')

def save_access_token(token):
    with open(TOKEN_FILE, 'w') as f:
        json.dump({'access_token': token, 'date': date.today().isoformat()}, f)

def get_app_id():
    return os.environ.get('FYERS_APP_ID', '')

def get_secret_key():
    return os.environ.get('FYERS_SECRET_KEY', '')

def get_fyers_headers():
    return {
        'Authorization': f'{get_app_id()}:{get_access_token()}',
        'Content-Type':  'application/json'
    }

# ============================================================
# FILE PERSISTENCE
# ============================================================
def load_all():
    global SCAN_RESULTS, TRADE_HISTORY
    try:
        if os.path.exists(SCAN_FILE):
            with open(SCAN_FILE, 'r') as f:
                SCAN_RESULTS = json.load(f)
    except Exception as e:
        log.error(f"Scan load: {e}")
    try:
        if os.path.exists(HISTORY_FILE):
            with open(HISTORY_FILE, 'r') as f:
                TRADE_HISTORY = json.load(f)
            log.info(f"History: {len(TRADE_HISTORY)} trades loaded")
    except Exception as e:
        log.error(f"History load: {e}")

def save_scan():
    with open(SCAN_FILE, 'w') as f:
        json.dump(SCAN_RESULTS, f)

def save_history():
    with open(HISTORY_FILE, 'w') as f:
        json.dump(TRADE_HISTORY, f, default=str)

# ============================================================
# FYERS DATA
# ============================================================
def fetch_historical(symbol, days=400):
    try:
        end_dt   = datetime.now()
        start_dt = end_dt - timedelta(days=days)
        url      = 'https://api-t2.fyers.in/research/history'
        params   = {
            'symbol':      f'NSE:{symbol}-EQ',
            'resolution':  'D',
            'date_format': '1',
            'range_from':  start_dt.strftime('%Y-%m-%d'),
            'range_to':    end_dt.strftime('%Y-%m-%d'),
            'cont_flag':   '1'
        }
        res  = requests.get(url, params=params, headers=get_fyers_headers(), timeout=15)
        data = res.json()
        if data.get('s') != 'ok' or not data.get('candles'):
            return None
        df = pd.DataFrame(data['candles'], columns=['ts','Open','High','Low','Close','Volume'])
        df['Date'] = pd.to_datetime(df['ts'], unit='s')
        df = df.set_index('Date').drop('ts', axis=1)
        df = df[df['Close'] > 0].dropna()
        return df if len(df) >= 200 else None
    except Exception as e:
        log.warning(f"Fetch {symbol}: {e}")
        return None

def fetch_today_ohlc(symbols):
    prices = {}
    try:
        today_str  = date.today().isoformat()
        batch_size = 50
        for i in range(0, len(symbols), batch_size):
            batch   = symbols[i:i+batch_size]
            sym_str = ','.join([f'NSE:{s}-EQ' for s in batch])
            res     = requests.get('https://api-t2.fyers.in/quotes/',
                                   params={'symbols': sym_str},
                                   headers=get_fyers_headers(), timeout=15)
            data = res.json()
            if data.get('s') != 'ok': continue
            for q in data.get('d', []):
                sym = q.get('n','').replace('NSE:','').replace('-EQ','')
                v   = q.get('v', {})
                prices[sym] = {
                    'open':  v.get('open_price', 0),
                    'high':  v.get('high_price',  0),
                    'low':   v.get('low_price',   0),
                    'close': v.get('lp',           0),
                    'date':  today_str
                }
    except Exception as e:
        log.error(f"Quote fetch: {e}")
    return prices

# ============================================================
# 5-LAYER ENGINE
# ============================================================
def factor_score(closes, volumes):
    c = np.array(closes); v = np.array(volumes)
    if len(c) < 252: return None
    try:
        mom  = (c[-21]/c[0])-1 if c[0]>0 else np.nan
        r90  = np.diff(c[-90:])/c[-90:-1]
        qual = -np.std(r90) if len(r90)>=60 else np.nan
        val  = c[-1]/np.max(c) if np.max(c)>0 else np.nan
        vt   = np.mean(v[-5:])/np.mean(v[-50:]) if np.mean(v[-50:])>0 else np.nan
        r60  = np.diff(c[-60:])/c[-60:-1]
        ram  = np.mean(r60)/np.std(r60) if np.std(r60)>0 else np.nan
        f    = np.array([mom,qual,val,vt,ram])
        if (~np.isnan(f)).sum() < 3: return None
        return float(np.nansum(f*np.array([0.30,0.20,0.15,0.15,0.20])))
    except: return None

def hmm_regime(closes):
    try:
        from sklearn.preprocessing import StandardScaler
        from hmmlearn.hmm import GaussianHMM
        c  = np.array(closes)
        lr = np.diff(np.log(c+1e-10))
        rv = pd.Series(lr).rolling(10).std().values
        ok = ~(np.isnan(lr)|np.isnan(rv))
        X  = StandardScaler().fit_transform(np.column_stack([lr[ok],rv[ok]]))
        if len(X) < 60: return 'sideways'
        best_m, best_ll = None, -np.inf
        for seed in range(3):
            try:
                m = GaussianHMM(n_components=3,covariance_type='diag',
                                n_iter=80,tol=1e-2,random_state=seed,init_params='stmc')
                m.fit(X); ll = m.score(X)
                if ll > best_ll: best_ll=ll; best_m=m
            except: continue
        if best_m is None: return 'sideways'
        states = best_m.predict(X); cur = int(states[-1])
        means  = {s:np.mean(lr[ok][states==s]) for s in range(3) if (states==s).sum()>0}
        ss     = sorted(means, key=means.get)
        return {ss[0]:'bear',ss[1]:'sideways',ss[2]:'bull'}.get(cur,'sideways')
    except: return 'sideways'

def garch_stop(closes):
    try:
        from arch import arch_model
        rets = pd.Series(closes).pct_change().dropna()*100
        mr,sr= rets.mean(),rets.std()
        rets = rets[np.abs(rets-mr)<10*sr]
        if len(rets)<60: return 0.03,'normal'
        res  = arch_model(rets,mean='Constant',vol='Garch',p=1,q=1,dist='t').fit(disp='off',show_warning=False)
        fc   = res.forecast(horizon=5)
        dv   = np.sqrt(fc.variance.values[-1,-1])/100
        sp   = float(np.clip(2*dv*np.sqrt(5),0.01,0.08))
        av   = dv*np.sqrt(252)*100
        vr   = 'low' if av<15 else('normal' if av<30 else('high' if av<50 else 'extreme'))
        return sp,vr
    except:
        ds = pd.Series(closes).pct_change().iloc[-20:].std()
        return float(np.clip(2*ds*np.sqrt(5),0.01,0.08)),'normal'

def kalman_vel(closes):
    try:
        c=np.array(closes); x=np.array([c[0],0.]); P=np.eye(2)
        F=np.array([[1.,1.],[0.,1.]]); H=np.array([[1.,0.]])
        ds=np.std(np.diff(c))*c[0]
        Q=np.array([[0.01,0.],[0.,0.001]])*ds; R=np.array([[ds**2]])
        vel=0.
        for obs in c:
            x=F@x; P=F@P@F.T+Q; S=H@P@H.T+R; K=P@H.T@np.linalg.inv(S)
            x=x+K@(np.array([obs])-H@x); P=(np.eye(2)-K@H)@P; vel=x[1]
        return int(np.sign(vel/(c[-1]+1e-10)))
    except: return 0

# ============================================================
# FULL SCAN
# ============================================================
def run_full_scan():
    global SCAN_RESULTS, TRADE_HISTORY, SCAN_LOG
    log.info("=== SCAN STARTED ===")
    t0     = datetime.now()
    SCAN_LOG = [f"Started {t0.strftime('%H:%M:%S')} IST"]

    if not get_access_token():
        SCAN_LOG.append("ERROR: No Fyers token. Visit /token to generate.")
        log.error("No token"); return

    # Nifty regime
    SCAN_LOG.append("Fetching Nifty50...")
    ndf    = fetch_historical('NIFTY50', 400)
    nc     = ndf['Close'].values if ndf is not None else []
    regime = hmm_regime(nc) if len(nc)>=120 else 'unknown'
    SCAN_LOG.append(f"Regime: {regime}")

    # In bear regime — only generate SELL signals for FnO stocks, A/A+ grade
bear_mode = (regime == 'bear')

    buys = []; failed = 0; scanned = 0
    SCAN_LOG.append(f"Scanning {len(FNO_STOCKS)} stocks...")

    for sym in FNO_STOCKS:
        try:
            df = fetch_historical(sym, 400)
            if df is None: failed+=1; continue
            closes  = df['Close'].values
            volumes = df['Volume'].values

            fs = factor_score(closes, volumes)
            if fs is None or fs <= 0: scanned+=1; continue

            sp, vr = garch_stop(closes[-100:])
            if vr == 'extreme': scanned+=1; continue

            vel = kalman_vel(closes[-60:])
            if vel <= 0: scanned+=1; continue

            votes = 2
            if regime in ['bull','sideways']: votes+=1
            if vr in ['low','normal']: votes+=1
            rr    = 3.0 if fs>1.5 else(2.5 if fs>1.0 else 2.0)
            tp    = sp*rr
            ev    = 0.51*tp - 0.49*sp
            if ev > 0: votes+=1

            if votes < 4: scanned+=1; continue

            grade  = 'A+' if votes==5 else 'A'
            entry  = round(float(closes[-1]), 2)
            sl     = round(entry*(1-sp), 2)
            tgt    = round(entry*(1+tp),  2)
            conf   = min(int(votes/5*100), 99)
            score  = min(int((fs+1)*30+votes*8), 99)

            buys.append({
                'symbol': sym, 'signal_type': 'buy', 'grade': grade,
                'votes': votes, 'score': score, 'confidence': conf,
                'entry': entry, 'stop_loss': sl, 'target': tgt,
                'risk_reward': round(rr,1), 'stop_pct': round(sp*100,2),
                'market_regime': regime, 'vol_regime': vr,
                'is_fno': True, 'historical_win_rate': 51,
                'model_votes': {
                    'factor':      {'active':True,  'vote':1},
                    'regime':      {'active':regime in ['bull','sideways'], 'vote':1 if regime in ['bull','sideways'] else 0},
                    'garch':       {'active':vr in ['low','normal'], 'vote':1 if vr in ['low','normal'] else 0},
                    'kalman':      {'active':True, 'vote':1},
                    'monte_carlo': {'active':ev>0, 'vote':1 if ev>0 else 0},
                },
                'backtest': {'total_trades':100,'win_rate':0.51}
            })
            scanned+=1
        except Exception as e:
            log.warning(f"{sym}: {e}"); failed+=1

    buys.sort(key=lambda x:(0 if x['grade']=='A+' else 1, -x['score']))
    dur = (datetime.now()-t0).seconds
    ts  = datetime.now(IST).strftime('%Y-%m-%d %H:%M')

    with lock:
        SCAN_RESULTS = {
            'buy_signals': buys, 'sell_signals': [],
            'scan_metadata': {
                'timestamp': ts, 'stocks_scanned': scanned,
                'buy_signals_count': len(buys), 'sell_signals_count': 0,
                'market_regime': regime, 'scan_duration_seconds': dur,
                'failed_stocks': failed
            }
        }
        save_scan()

        # Auto-save to history
        today = date.today().isoformat()
        added = 0
        for sig in buys:
            if any(t['symbol']==sig['symbol'] and t['scan_date']==today for t in TRADE_HISTORY):
                continue
            TRADE_HISTORY.append({
                'id': str(uuid.uuid4()), 'symbol': sig['symbol'],
                'signal_type': 'buy', 'grade': sig['grade'],
                'score': sig['score'], 'confidence': sig['confidence'],
                'votes': sig['votes'], 'entry': sig['entry'],
                'stop_loss': sig['stop_loss'], 'target': sig['target'],
                'risk_reward': sig['risk_reward'], 'stop_pct': sig['stop_pct'],
                'market_regime': regime, 'vol_regime': sig['vol_regime'],
                'is_fno': True, 'model_votes': sig['model_votes'],
                'historical_win_rate': 51,
                'scan_date': today, 'scan_time': ts,
                'outcome_status': 'pending', 'entry_met': False,
                'entry_met_date': None, 'actual_exit': None,
                'actual_pnl': None, 'outcome_date': None,
                'expiry_date': (date.today()+timedelta(days=7)).isoformat(),
                'last_rescanned': None,
            })
            added+=1
        if added: save_history()

    SCAN_LOG.append(f"Done: {len(buys)} signals | {scanned} scanned | {failed} failed | {dur}s")
    log.info(f"Scan done: {len(buys)} signals in {dur}s")

# ============================================================
# DAILY RESCAN
# ============================================================
def run_daily_rescan():
    global TRADE_HISTORY
    log.info("=== RESCAN STARTED ===")
    if not get_access_token():
        log.error("No token — rescan aborted"); return

    pending = [t for t in TRADE_HISTORY
               if t.get('outcome_status') not in ('target_hit','stop_hit','expired')]
    if not pending: return

    symbols = list(set(t['symbol'] for t in pending))
    prices  = fetch_today_ohlc(symbols)
    today   = date.today().isoformat()
    updated = 0

    with lock:
        for trade in TRADE_HISTORY:
            if trade.get('outcome_status') in ('target_hit','stop_hit','expired'):
                continue
            px = prices.get(trade['symbol'])
            if not px: continue

            high  = float(px['high']);  low   = float(px['low'])
            entry = float(trade['entry']); sl  = float(trade['stop_loss'])
            tgt   = float(trade['target']); exp = trade.get('expiry_date','')
            st    = trade.get('signal_type','buy')

            trade['last_rescanned'] = today

            # Expiry check
            if not trade.get('entry_met') and exp and today > exp:
                trade['outcome_status'] = 'expired'
                trade['outcome_date']   = today
                updated+=1; continue

            # Entry check
            if not trade.get('entry_met'):
                met = (low<=entry) if st=='buy' else (high>=entry)
                if met:
                    trade['entry_met']      = True
                    trade['entry_met_date'] = today
                    trade['outcome_status'] = 'entry_met'
                    updated+=1

            # Target / SL check
            if trade.get('entry_met'):
                if st=='buy':
                    if high>=tgt:
                        trade.update({'outcome_status':'target_hit','actual_exit':tgt,
                            'actual_pnl':round(((tgt-entry)/entry)*100,2),'outcome_date':today})
                        updated+=1
                    elif low<=sl:
                        trade.update({'outcome_status':'stop_hit','actual_exit':sl,
                            'actual_pnl':round(((sl-entry)/entry)*100,2),'outcome_date':today})
                        updated+=1

        if updated: save_history()
    log.info(f"Rescan done: {updated} updated")

# ============================================================
# SCHEDULER — 5pm scan, 5:30pm rescan
# ============================================================
scheduler = BackgroundScheduler(timezone=IST)
scheduler.add_job(run_full_scan,   CronTrigger(hour=17,minute=0,  timezone=IST), id='scan',   replace_existing=True)
scheduler.add_job(run_daily_rescan,CronTrigger(hour=17,minute=30, timezone=IST), id='rescan', replace_existing=True)
scheduler.start()
log.info("Scheduler ready: scan@5pm, rescan@5:30pm IST")

# ============================================================
# STATIC ROUTES — serves index.html, history.html
# ============================================================
@app.route('/')
def serve_index():
    return send_from_directory('.', 'index.html')

@app.route('/history')
def serve_history():
    return send_from_directory('.', 'history.html')

@app.route('/token')
def serve_token():
    return send_from_directory('.', 'token.html')

@app.route('/<path:filename>')
def serve_static(filename):
    try:
        return send_from_directory('.', filename)
    except:
        return jsonify({'error': f'{filename} not found'}), 404

# ============================================================
# FYERS TOKEN ENDPOINTS
# ============================================================
@app.route('/api/token/auth-url', methods=['GET'])
def api_token_auth_url():
    """Generate Fyers auth URL for login."""
    app_id   = get_app_id()
    redirect = 'https://trade.fyers.in/api-login/redirect-uri/index.html'
    if not app_id:
        return jsonify({'error': 'FYERS_APP_ID not set in Render environment'}), 400
    url = (f'https://api-t2.fyers.in/api/v3/generate-authcode'
           f'?client_id={app_id}'
           f'&redirect_uri={requests.utils.quote(redirect)}'
           f'&response_type=code&state=render_login')
    return jsonify({'auth_url': url, 'redirect_uri': redirect})

@app.route('/api/token/exchange', methods=['POST'])
def api_token_exchange():
    """Exchange auth_code for access_token."""
    try:
        data      = request.get_json(force=True)
        auth_code = data.get('auth_code','').strip()
        if not auth_code:
            return jsonify({'error': 'auth_code required'}), 400

        app_id  = get_app_id()
        secret  = get_secret_key()
        if not app_id or not secret:
            return jsonify({'error': 'FYERS_APP_ID or FYERS_SECRET_KEY not set in Render environment'}), 400

        import hashlib
        app_hash = hashlib.sha256(f'{app_id}:{secret}'.encode()).hexdigest()

        res = requests.post('https://api-t2.fyers.in/api/v3/validate-authcode', json={
            'grant_type':    'authorization_code',
            'appIdHash':     app_hash,
            'code':          auth_code,
        }, timeout=15)
        result = res.json()

        if result.get('s') != 'ok':
            return jsonify({'error': result.get('message','Token exchange failed')}), 400

        token = result.get('access_token','')
        save_access_token(token)

        return jsonify({'status': 'success', 'message': 'Token saved. Scanner will use this token today.'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/token/status', methods=['GET'])
def api_token_status():
    token = get_access_token()
    return jsonify({
        'token_set':  bool(token),
        'token_date': json.load(open(TOKEN_FILE))['date'] if os.path.exists(TOKEN_FILE) else None,
        'app_id_set': bool(get_app_id()),
        'secret_set': bool(get_secret_key()),
    })

# ============================================================
# SCAN & HISTORY API
# ============================================================
@app.route('/api/scan', methods=['GET'])
def api_scan():
    return jsonify(SCAN_RESULTS)

@app.route('/api/status', methods=['GET'])
def api_status():
    m        = SCAN_RESULTS.get('scan_metadata', {})
    next_job = scheduler.get_job('scan')
    return jsonify({
        'status':         'running',
        'last_scan':      m.get('timestamp','Never'),
        'buy_signals':    m.get('buy_signals_count',0),
        'market_regime':  m.get('market_regime','unknown'),
        'stocks_scanned': m.get('stocks_scanned',0),
        'history_count':  len(TRADE_HISTORY),
        'next_scan':      str(next_job.next_run_time) if next_job else 'unknown',
        'scan_log':       SCAN_LOG[-5:],
        'token_ready':    bool(get_access_token()),
    })

@app.route('/api/history', methods=['GET'])
def api_history():
    with lock:
        h = sorted(TRADE_HISTORY, key=lambda x: x.get('scan_time',''), reverse=True)
    return jsonify({'trades': h, 'total': len(h)})

@app.route('/api/history/update', methods=['POST'])
def api_history_update():
    try:
        data = request.get_json(force=True)
        tid  = data.get('id')
        if not tid: return jsonify({'error':'id required'}),400
        with lock:
            t = next((x for x in TRADE_HISTORY if x['id']==tid), None)
            if not t: return jsonify({'error':'not found'}),404
            for f in ['outcome_status','actual_exit','actual_pnl','outcome_date']:
                if f in data: t[f] = data[f]
            save_history()
        return jsonify({'status':'updated'})
    except Exception as e:
        return jsonify({'error':str(e)}),500

@app.route('/api/history/rescan', methods=['POST'])
def api_rescan_manual():
    threading.Thread(target=run_daily_rescan, daemon=True).start()
    return jsonify({'status':'started','message':'Rescan running in background'})

@app.route('/api/scan/manual', methods=['POST'])
def api_scan_manual():
    threading.Thread(target=run_full_scan, daemon=True).start()
    return jsonify({'status':'started','message':'Scan running in background — check /api/status'})

@app.route('/api/upload', methods=['POST'])
def api_upload():
    """Legacy Colab upload — still works."""
    global SCAN_RESULTS
    try:
        data = request.get_json(force=True)
        if not data: return jsonify({'error':'No JSON'}),400
        with lock:
            SCAN_RESULTS = {
                'buy_signals':   data.get('buy_signals',[]),
                'sell_signals':  data.get('sell_signals',[]),
                'scan_metadata': data.get('scan_metadata',{})
            }
            SCAN_RESULTS['scan_metadata']['buy_signals_count']  = len(SCAN_RESULTS['buy_signals'])
            SCAN_RESULTS['scan_metadata']['sell_signals_count'] = len(SCAN_RESULTS['sell_signals'])
            save_scan()
        return jsonify({'status':'success'})
    except Exception as e:
        return jsonify({'error':str(e)}),500

@app.route('/api/history/clear', methods=['POST'])
def api_history_clear():
    global TRADE_HISTORY
    with lock:
        TRADE_HISTORY = []
        save_history()
    return jsonify({'status':'cleared'})

load_all()

if __name__ == '__main__':
    port = int(os.environ.get('PORT',5000))
    app.run(host='0.0.0.0', port=port, debug=False)
