# Tender Watcher web app

FastAPI + Postgres. Server-rendered pages, phone-friendly.

- Collectors (freelancers) sign in, photograph a notice, Claude fills in the fields, they check them.
- Reviewers approve or reject; approved tenders are published on the site.
- Admin adds collectors and reviewers and sees counts per collector for pay.

Run tests: `cd app && python -m pytest -q`

## Deploy on ghion (first time)

```sh
# 1. Code and Python packages (same venv as the entry bot)
sudo mkdir -p /opt/tenders/app /opt/tenders/app-data
sudo cp -r app/tw /opt/tenders/app/
sudo /opt/tenders/venv/bin/pip install -r app/requirements.txt

# 2. Postgres, on localhost:5442 only
sudo cp app/deploy/db-compose.yml /opt/tenders/db-compose.yml
sudo TW_DB_PASSWORD='...' docker compose -f /opt/tenders/db-compose.yml up -d

# 3. Settings: copy app/deploy/app.env.example to /opt/tenders/app.env, fill in, chmod 600

# 4. Service, then the first admin
sudo cp app/deploy/tenders-app.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now tenders-app
cd /opt/tenders/app && sudo bash -c 'set -a; . /opt/tenders/app.env; /opt/tenders/venv/bin/python -m tw.manage create-admin --name "Robel" --phone 09...'
sudo bash -c 'set -a; . /opt/tenders/app.env; cd /opt/tenders/app; /opt/tenders/venv/bin/python -m tw.manage import-board /opt/tenders/site/tenders.json'

# 5. nginx: add app/deploy/nginx-app.conf to the tenders.sonador.et server block, then
sudo nginx -t && sudo systemctl reload nginx
```
