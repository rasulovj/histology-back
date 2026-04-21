# AI Study Assistant — GitHub + Railway deployment pack

This pack contains the minimum files you should place in the root of your repository before pushing to GitHub and deploying on Railway.

## Files in this pack
- `.gitignore` — prevents secrets and runtime data from being committed
- `.env.example` — template of required environment variables
- `requirements.txt` — Python dependencies based on the code you shared
- `Procfile` — fallback worker start command
- `railway.json` — Railway config-as-code start command

## What to do before pushing to GitHub
1. Put these files in the project root.
2. Make sure your real `.env` exists locally but is NOT committed.
3. Create these folders locally if your app expects them at runtime:
   - `data/`
   - `data/raw_files/`
   - `data/knowledge_base/`
4. Confirm your entrypoint is `main.py` in the repository root.

## Recommended project root layout
```text
project-root/
├── main.py
├── config.py
├── requirements.txt
├── .gitignore
├── .env.example
├── railway.json
├── Procfile
├── handlers/
├── services/
└── data/
```

## Required Railway variables
Add these in Railway → Service → Variables:
- `BOT_TOKEN`
- `GEMINI_KEY`
- `DEEPSEEK_KEY`
- `PAYMENT_TOKEN`
- `ADMIN_ID`

If you do not use some optional features, you can leave these empty:
- `DEEPSEEK_KEY`
- `PAYMENT_TOKEN`

## Railway deployment steps
1. Push the repository to GitHub.
2. In Railway, create a new project.
3. Choose **Deploy from GitHub repo**.
4. Select your repository.
5. Railway should detect Python automatically.
6. Add the required variables.
7. Deploy.
8. If Railway asks for a start command, use:
   ```bash
   python main.py
   ```

## Important limitation in your current architecture
Your bot writes runtime files to `data/` and also stores SQLite in `data/users.db`.
On many cloud platforms, local filesystem contents can be ephemeral across restarts or redeploys.

That means these can be lost after redeploy/restart:
- `users.db`
- generated PDFs
- temporary images
- uploaded txt tests
- cached knowledge files generated at runtime

## Strong recommendation for next stage
After first successful deploy:
- move user database from SQLite to PostgreSQL
- move persistent files to object storage / bucket
- keep only temporary files on local disk

## GitHub push example
```bash
git init
git add .
git commit -m "Prepare AI Study Assistant for GitHub and Railway"
git branch -M main
git remote add origin YOUR_GITHUB_REPO_URL
git push -u origin main
```

## Railway CLI alternative
```bash
railway init
railway up
```

## Notes
- `railway.json` uses Railway config-as-code so the start command travels with the repo.
- `Procfile` is included as a fallback, although Railway prefers configuring the start command directly or via config-as-code.
