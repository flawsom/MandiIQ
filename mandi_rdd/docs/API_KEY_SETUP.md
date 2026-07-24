# 🔑 Production API Keys Guide for MandiIQ

This guide explains how to set up production-ready API keys for MandiIQ. Two services require keys: Government data APIs and AI orchestration.

---

## 🔐 API Key Requirements

| Service | Purpose | Registration Required? | Default Key Allowed? |
|---------|---------|----------------------|---------------------|
| **data.gov.in** | Mandi prices + IMD rainfall data | ✅ Yes (recommend) | ⚠️ Demo key (rate-limited) |
| **OpenRouter** | AI orchestrator (phase 11) | ✅ Yes (recommend) | ❌ No, must register |

---

## 📦 Part 1: Government API Key (data.gov.in)

### Why You Need It

The public demo key on the API code is **rate-limited to ~100 requests/day**. For production use with full functionality:

- Unlimited request volume
- Higher priority access
- No waiting during peak hours
- No errors from rate-limit hits
- Suitable for nightly cron + dashboard usage

### Step-by-Step Setup

#### 1. Create Account at data.gov.in

1. Go to: https://api.data.gov.in/
2. Click **"Get Started"** → Create account
3. Verify your email address

#### 2. Register Your API Key

1. Log in to https://api.data.gov.in/manage
2. Click **My Account** (top right)
3. Click **API Keys** tab
4. Click **Create API Key**
5. Name: `MandiIQ Production`
6. Use Case: "Commodity price analysis pipeline"
7. Purpose: "Research & development of causal inference models"
8. Click **Create Key**

#### 3. Copy Your API Key

Your API key will be displayed in a single-use popup:
```
Your new key is: 579b464db66ec23bdd000001cdd3946e44ce4aad7209ff7b23ac571b
```

**⚠️ Save this key immediately** — it will be hidden after closing.

#### 4. Configure MandiIQ

**Option A: Environment Variable (Recommended)**

```bash
# Add to your .env file
echo "DATA_GOV_IN_API_KEY=your_actual_api_key_here" >> .env
```

**Option B: Render Dashboard**

1. Open Render Dashboard
2. Go to your `mandi-iq-dashboard` service
3. Click **Service** → **Environment**
4. Add new environment variable:
   - Name: `DATA_GOV_IN_API_KEY`
   - Value: `your_actual_api_key_here` (paste your key)
5. Click **Save**
6. Click **Deploy latest commit** to reload

**Option C: Render API Service**

1. Open Render Dashboard
2. Go to your `mandi-iq-api` service
3. Click **Service** → **Environment**
4. Add new environment variable:
   - Name: `DATA_GOV_IN_API_KEY`
   - Value: `your_actual_api_key_here`
5. Click **Save**
6. Click **Deploy latest commit** to reload

**Option D: Local Development**

```bash
# On Windows PowerShell
$Env:DATA_GOV_IN_API_KEY="your_actual_api_key_here"

# On Linux/Mac
export DATA_GOV_IN_API_KEY="your_actual_api_key_here"
```

#### 5. Verify It Works

```bash
# Test the API
curl -X POST https://mandi-iq-api-XXXX.onrender.com/refresh \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "commodity=Onion"

# Should return:
# {"status":"ok","message":"Pipeline complete: ...","duration_seconds":120.5}
```

### Troubleshooting

**Problem**: `data.gov.in` returns 403 Forbidden

**Solution**:
- ✅ `DATA_GOV_IN_API_KEY` is set correctly
- ✅ Key matches what you registered for
- ✅ Account is activated (check spam folder)
- ✅ Key hasn't expired (never expires)

**Problem**: Rate limit still hit (429 error)

**Solution**:
- ✅ Verify you're using your registered key
- ✅ Reduce polling frequency (nightly cron is fine)
- ✅ Check pending invoices or account status

**Problem**: Pipeline shows "No data available"

**Solution**:
1. Verify API key is set in both Dashboard AND API services on Render
2. Click **"Deploy latest commit"** after adding each environment variable
3. Run `curl -X POST https://your-api-url/refresh` manually
4. Check Render logs for errors

---

## 🤖 Part 2: AI API Key (OpenRouter)

### Why You Need It

OpenRouter provides free AI models with circuit-breaker fallback. Key is required for:
- **Nightly narrative generation** (3-4 sentence summary)
- **"Ask MandiIQ" chat panel** on dashboard (Phase 11)

**Note**: Core MandiIQ functionality works WITHOUT this key. The AI features are enhancements.

### Step-by-Step Setup

#### 1. Create Account at openrouter.ai

1. Go to: https://openrouter.ai/keys
2. Click **"Sign Up"** → Sign in with GitHub
3. Verify your email (if required)

#### 2. Generate API Key

1. Log in to https://openrouter.ai/keys
2. Click **"Generate Key"**
3. Name: `MandiIQ Production`
4. Click **Generate Key**

Your API key will be displayed:
```
sk-or-v1-your_generated_key_here (starts with sk-or-v1-)
```

**⚠️ Save this key immediately** — similar to data.gov.in.

#### 3. Configure MandiIQ

**Option A: Environment Variable (Recommended)**

```bash
# Add to your .env file
echo "OPENROUTER_API_KEY=sk-or-v1-your_generated_key_here" >> .env
```

**Option B: Render Dashboard**

1. Open Render Dashboard
2. Go to your `mandi-iq-dashboard` service
3. Click **Service** → **Environment**
4. Add new environment variable:
   - Name: `OPENROUTER_API_KEY`
   - Value: `sk-or-v1-your_generated_key_here`
5. Click **Save**
6. Click **Deploy latest commit**

**Option C: Local Development**

```bash
# On Windows PowerShell
$Env:OPENROUTER_API_KEY="sk-or-v1-your_generated_key_here"

# On Linux/Mac
export OPENROUTER_API_KEY="sk-or-v1-your_generated_key_here"
```

#### 4. Verify It Works

```bash
# Test AI chat endpoint
curl -X POST https://mandi-iq-api-XXXX.onrender.com/ask \
  -H "Content-Type: application/json" \
  -d '{"query":"Should I lock in onion procurement in Nashik next month?"}'

# Should return grounded answer with:
# "answer": "Moderate risk (32%)...",
# "model_used": "gemini-2.0-flash-exp:free"
```

### Troubleshooting

**Problem**: AI chat shows "All models exhausted"

**Cause**: Free-tier rate limits hit

**Solution**:
- Wait a few minutes (models enter cooling-down phase)
- Check http://openrouter.ai/keys to see your usage
- The circuit-breaker automatically retries next model

**Problem**: AI chat shows "OpenRouter module not available"

**Cause**: `openai` SDK not installed

**Solution**:
```bash
pip install openai
# Or for container:
RUN pip install openai
```

**Problem**: Nightly narrative not being generated

**Cause**: Either:
1. `OPENROUTER_API_KEY` not set
2. No data available (run `/refresh` first)

**Solution**:
```bash
# 1. Run refresh to populate data
curl -X POST https://your-api-url/refresh

# 2. Verify OPENROUTER_API_KEY is set
# 3. Check Render logs:
# Go to Render Dashboard → your-service → Logs
```

---

## 📊 API Key Comparison

| Platform | Key Format | Rate Limits | Production Recommended |
|----------|-----------|-------------|------------------------|
| data.gov.in | `579b...ac571b` | 100/day (demo) | ✅ Register for unlimited |
| OpenRouter | `sk-or-v1-...` | Free-tier daily cap | ✅ Register for higher limits |

---

## 🛡️ Security Best Practices

### 🔒 Environment Variables (Never Commit)

```bash
# ❌ NEVER commit .env file to git
# .env is in .gitignore

# ✅ Set via:
#   - Render Dashboard (Environment tab)
#   - Docker environment
#   - Kubernetes secrets
```

### 🔐 Key Storage

**Never use these in code:**
```python
# ❌ BAD: Hardcoded in source code
API_KEY = "579b464db66ec23bdd000001cdd3946e44ce4aad7209ff7b23ac571b"

# ✅ GOOD: Load from environment
API_KEY = os.getenv("DATA_GOV_IN_API_KEY")
```

### 🔄 Key Rotation

API keys generally don't expire, but should be rotated if:
- Key is leaked/stolen
- Account security compromised
- Using compromised account for research

**Rotation Process:**
1. Create new key on platform
2. Update environment variable
3. Deploy restart
4. Delete old key

---

## 📋 Checklist

### Before Production Deploy

- [ ] **data.gov.in key**: Registered and set in Render (both API + Dashboard)
- [ ] **OpenRouter key**: Generated and set in Render Dashboard
- [ ] Both services show blue dot in Render Environment (active)
- [ ] Click **"Deploy latest commit"** after each env var change
- [ ] Test `/health` endpoint returns healthy
- [ ] Test `/refresh` endpoint processes successfully
- [ ] Test `/ask` endpoint returns grounded answer (if OpenRouter key set)
- [ ] Check nightly cron is running (Render Dashboard → Cron)

### After Production Deploy

- [ ] Schedule shows on GitHub Actions (daily at 6 AM UTC)
- [ ] Health check returns data counts > 0
- [ ] Dashboard shows prices > 0 rows in database
- [ ] Nightly narrative visible on Executive Overview page (if OpenRouter key set)
- [ ] No 429 errors in Render logs

---

## 🌍 Alternative: No Keys Version (Demo-Only)

If you DON'T register keys:

### For data.gov.in
- Use default key in code (limited to ~100/day)
- Run pipeline once per day (don't poll constantly)
- Falls back gracefully on rate limits

### For OpenRouter
- Set `OPENROUTER_API_KEY=""` (empty)
- "Ask MandiIQ" chat panel shows: "AI orchestrator module is not available"
- Nightly narrative is skipped (no narrative tab on dashboard)
- Core causal + predictive + prescriptive features still work perfectly

**Result**: Fully functional MandiIQ without API keys, but limited to demo usage.

---

## 📞 Support

**data.gov.in Docs**: https://api.data.gov.in/docs
**OpenRouter Docs**: https://openrouter.ai/docs
**Render Dashboard**: https://dashboard.render.com

Check logs in Render Dashboard if issues persist:
1. Go to Service → Logs tab
2. Filter by timestamp
3. Look for errors in ingestion/forecast analysis

---

**Last Updated**: 2025-07-17
**Version**: 1.0.0
**Repository**: Margin-Intelligence-System/mandi_rdd