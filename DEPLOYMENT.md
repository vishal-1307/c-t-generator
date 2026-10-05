# Deployment Guide: College Timetable Generator

This guide details how to deploy the **College Timetable Generator** into production:
- **Frontend**: Next.js 16 deployed on **Vercel**
- **Backend**: FastAPI + Google OR-Tools CP-SAT deployed on a persistent container service (**Render** or **Railway**)
- **Database**: Managed PostgreSQL (**Neon.tech** or **Render Postgres**)

---

## 1. Architecture & Why Backend Hosting is Different

```
┌─────────────────────────┐               ┌────────────────────────────────────────┐
│     Next.js Frontend    │ ────────────> │            FastAPI Backend             │
│        (Vercel)         │  HTTPS / API  │ (Render / Railway Web Service Docker)  │
└─────────────────────────┘               └────────────────────────────────────────┘
                                                               │
                                                               ▼
                                                  ┌────────────────────────┐
                                                  │ PostgreSQL (Neon/Cloud)│
                                                  └────────────────────────┘
```

> [!IMPORTANT]
> **Why the Backend Cannot Run on Vercel Functions / AWS Lambda:**
> The timetable generation algorithm uses **Google OR-Tools CP-SAT**, which executes mathematically complex constraint satisfaction solves in a background thread for **2 to 60 seconds**.
> 
> Serverless functions (like Vercel serverless / AWS Lambda) terminate or freeze worker threads as soon as the initial HTTP response is returned. This would abort the timetable solve halfway through.
> 
> Therefore, the backend **must be hosted on a persistent web service or container platform** such as **Render**, **Railway**, **Fly.io**, or a virtual server.

---

## 2. Step 1: Set Up Free PostgreSQL Database

We recommend **Neon.tech** (free serverless PostgreSQL with 0.5 GB storage, always free) or **Render PostgreSQL**.

### Using Neon.tech (Recommended):
1. Go to [https://neon.tech](https://neon.tech) and sign up/sign in with GitHub.
2. Click **Create Project** (Name it `collegetimetable`).
3. Under **Connection Details**, select **Connection string** (Pooled or Direct).
4. Copy the connection string. It will look like:
   ```text
   postgresql://alex:AbCdEf123456@ep-cool-cloud-123456.us-east-2.aws.neon.tech/collegetimetable?sslmode=require
   ```
   *(Note: The backend automatically normalizes `postgresql://` and `postgres://` to the correct psycopg driver).*

---

## 3. Step 2: Deploy Backend to Render

Render offers an easy, reliable Docker web service deployment.

### 1. Create Web Service on Render:
1. Log in to [https://dashboard.render.com](https://dashboard.render.com).
2. Click **New +** > **Web Service**.
3. Select **Build and deploy from a Git repository** and connect your GitHub account.
4. Select `vishal-1307/c-t-generator`.
5. Configure the service settings:
   - **Name**: `c-t-generator-api` (or any name you choose)
   - **Region**: Choose the closest region to you (e.g., Singapore, Frankfurt, Oregon, Ohio)
   - **Branch**: `main`
   - **Root Directory**: `backend`
   - **Runtime**: **Docker** (Render will automatically detect `backend/Dockerfile`)
   - **Instance Type**: **Free** (or Starter/Standard for higher memory)

### 2. Configure Environment Variables on Render:
Scroll down to the **Environment Variables** section and add the following keys:

| Key | Example Value | Description |
|---|---|---|
| `APP_ENV` | `production` | Enables production security guards |
| `DATABASE_URL` | `postgresql://user:pass@ep-xyz.neon.tech/collegetimetable?sslmode=require` | Your Neon or PostgreSQL connection URI |
| `JWT_SECRET_KEY` | *(generate random 64-char key)* | Secret key for signing login tokens |
| `BOOTSTRAP_ADMIN_USERNAME` | `admin` | Initial administrator username |
| `BOOTSTRAP_ADMIN_PASSWORD` | `YourSecurePassword123!` | Must be at least 12 characters long |
| `CORS_ORIGINS` | `https://your-frontend.vercel.app` | Exact URL of your frontend (set this after Vercel deployment) |
| `GEMINI_API_KEY` | `AIzaSy...` | *(Optional)* Google Gemini API key for the AI assistant |
| `GEMINI_MODEL` | `gemini-3.6-flash` | Latest active production model for AI Studio (default: `gemini-3.6-flash`) |

> [!TIP]
> **Generating a secure `JWT_SECRET_KEY`:**
> Run this in your local terminal:
> ```bash
> python -c "import secrets; print(secrets.token_urlsafe(48))"
> ```
> Copy the resulting string and paste it into `JWT_SECRET_KEY` on Render.

### 3. Deploy:
Click **Create Web Service**.
Render will build the Docker container, run `alembic upgrade head` to automatically create all database tables, and start Uvicorn.

When the build finishes, you will receive a backend URL, such as:
`https://c-t-generator-api.onrender.com`

Verify it by visiting:
`https://c-t-generator-api.onrender.com/api/v1/health`  
*(It should return `{"status":"ok", ...}`)*

---

## 4. Step 3: Deploy Frontend to Vercel

1. Log in to [https://vercel.com](https://vercel.com).
2. Click **Add New...** > **Project**.
3. Import your repository: `vishal-1307/c-t-generator`.
4. Configure the Project Settings:
   - **Framework Preset**: Next.js (auto-detected)
   - **Root Directory**: Click **Edit** and choose `frontend`
5. In **Environment Variables**, add:
   - **Name**: `NEXT_PUBLIC_API_URL`
   - **Value**: `https://c-t-generator-api.onrender.com` *(your backend URL without trailing slash)*
6. Click **Deploy**.

Vercel will build and deploy the Next.js frontend in approximately 1-2 minutes.

---

## 5. Step 4: Final Link (CORS Configuration)

Once your Vercel deployment completes, Vercel gives you your production URL (e.g., `https://c-t-generator.vercel.app`).

1. Open your **Render** dashboard -> `c-t-generator-api` -> **Environment**.
2. Find `CORS_ORIGINS`.
3. Set it to your exact Vercel URL:
   ```text
   https://c-t-generator.vercel.app
   ```
   *(Important: Do NOT include a trailing slash `/` or path. It must be `scheme://host` exactly).*
4. Click **Save Changes**. Render will automatically restart the backend service.

---

## 6. Alternative: Deploy Backend to Railway

If you prefer Railway:
1. Go to [https://railway.app](https://railway.app) and click **New Project**.
2. Select **Provision PostgreSQL** to create an instant database.
3. Click **+ New** > **GitHub Repo** > Select `c-t-generator`.
4. Under Service Settings:
   - **Root Directory**: `/backend`
   - **Build**: Uses `backend/Dockerfile`
5. Add the same Environment Variables (`APP_ENV`, `DATABASE_URL` via Railway's `${{Postgres.DATABASE_URL}}`, `JWT_SECRET_KEY`, `CORS_ORIGINS`, etc.).
6. Generate a public domain under **Networking**.

---

## 7. Alternative: Deploy Backend to Zoho Catalyst (AppSail)

**Can you host on Zoho Catalyst?**
**Yes!** Zoho Catalyst is free for students and offers **900 GB-minutes/month of AppSail runtime** on its permanent free tier.

> [!CAUTION]
> **Important Distinction in Zoho Catalyst:**
> - **DO NOT** use **Catalyst Functions (Serverless)**: Standard serverless functions freeze/terminate background threads upon sending an HTTP response, which kills CP-SAT solver jobs.
> - **DO USE** **Catalyst AppSail**: AppSail is Zoho Catalyst's standalone PaaS container service designed for long-running frameworks like FastAPI.

### Steps for Zoho Catalyst AppSail:
1. **Database**: Use [Neon.tech](https://neon.tech) for your free PostgreSQL instance (Catalyst's built-in Data Store is proprietary NoSQL/relational and does not support standard SQLAlchemy/Alembic).
2. **Install Catalyst CLI** (optional for local deployment):
   ```bash
   npm install -g zcatalyst-cli
   catalyst login
   ```
3. **Deploy with AppSail Console or CLI**:
   - In the Zoho Catalyst Console, create a new Project.
   - Go to **AppSail** > **Create AppSail**.
   - Select **Custom Runtime (Docker)** or deploy the `backend/Dockerfile` image.
   - Port handling: The container automatically handles Catalyst's `X_ZOHO_CATALYST_LISTEN_PORT`.
4. **Set Environment Variables in Catalyst Console**:
   - `APP_ENV`: `production`
   - `DATABASE_URL`: Your PostgreSQL connection string from Neon.tech
   - `JWT_SECRET_KEY`: Your generated random secret
   - `BOOTSTRAP_ADMIN_USERNAME`: `admin`
   - `BOOTSTRAP_ADMIN_PASSWORD`: Your password (min 12 chars)
   - `CORS_ORIGINS`: Your Vercel frontend URL
5. **Get AppSail URL**: Copy the public URL generated by Catalyst and set it as `NEXT_PUBLIC_API_URL` on Vercel.

---

## 8. Verification Checklist

After deploying both frontend and backend:
1. Open your Vercel application URL.
2. Sign in with:
   - **Username**: `admin` (or what you configured in `BOOTSTRAP_ADMIN_USERNAME`)
   - **Password**: The password you configured in `BOOTSTRAP_ADMIN_PASSWORD`
3. Click **Upload** or use existing infrastructure/load data.
4. Click **Analyze Files** -> Verify readiness summary.
5. Click **Generate Timetable** -> Verify that OR-Tools CP-SAT generates a 0-conflict schedule.
6. Check **Timetable Views** (By Section, By Faculty, By Room, Master Timetable).
7. Test the **AI Assistant** drawer (bottom right) -> Ask questions like `"Which section has the most lab hours?"` or `"Show Section 2401 Friday schedule"`.
8. Download the **Excel Export** workbook and verify all 4 sheets.
