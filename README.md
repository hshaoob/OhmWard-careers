# Ohmward Careers ⚡

A job board for **electrical engineering internships, co-ops and new grad roles** that just dropped. Every 15 minutes it checks 5,000+ company career sites directly, keeps only US EE roles that are still open and under 14 days old, and emails subscribers the new ones that match their filters.

Built on [ee-jobscanner](https://github.com/hshaoob/ee-jobscanner): same scanner, now with a website and many subscribers.

## What visitors get

- **Five EE areas, explained simply.** Power, communications, controls, signal processing, and computer systems. Pick any, or none to see everything.
- **Filters:** role type, term or graduating class, on-site / hybrid / remote, states, minimum pay, how long the application takes (Greenhouse is quick, Workday is long), and eligibility (citizenship or clearance required, no visa sponsorship).
- **Favorite companies** are listed first. Their roles are emailed right away, even outside your other filters.
- **Skills:** tick yours to sort by best fit and see which of your skills a role asks for.
- **Applied / Hide** buttons, saved in your browser.
- **Email alerts** use the same filters as the board. You choose instant or a daily summary at 8am PT, confirm with an emailed link, change your alerts with an emailed link (no password), and unsubscribe in one click.
- **Why only fresh roles,** with a real number measured by this site: how many roles close within a week.

## How it works

```
GitHub Actions, every 15 minutes
  jobscan.py: scan boards → verify each posting → docs/jobs.json → email matching subscribers
GitHub Pages
  docs/index.html: reads jobs.json, filters in the browser, signs people up
Supabase (free tier)
  sign-in links + the subscribers table (emails never live in this public repo)
```

## Set it up (one time, about 30 minutes)

1. **Repo.** Push this folder to a **public** GitHub repo. Public repos get unlimited Actions minutes; a private one would run out in days.
2. **Supabase.** Create a project at [supabase.com](https://supabase.com).
   - **SQL Editor:** paste `supabase.sql` and click **Run**.
   - **Authentication → URL Configuration:** set **Site URL** to your Pages address (`https://<you>.github.io/<repo>/`) and add `https://<you>.github.io/<repo>/alerts.html` under **Redirect URLs**.
   - **Project Settings → API:** copy the **Project URL** and the **anon public** key into the top of `docs/common.js`.
3. **Sending email.** Create a [Resend](https://resend.com) account, verify a domain you own (Resend only sends to strangers from a verified domain), and create an API key.
   - In Supabase, go to **Authentication → Emails → SMTP Settings** and turn on custom SMTP: host `smtp.resend.com`, port `465`, user `resend`, password = the API key. The built-in sender only allows a few emails an hour, so sign-ups need this.
4. **GitHub secrets** (Settings → Secrets and variables → Actions):

   | Name | Value |
   |---|---|
   | `SUPABASE_URL` | Project URL |
   | `SUPABASE_SERVICE_KEY` | the **service_role** key (secret: never put it in the website) |
   | `SMTP_PASS` | Resend API key |
   | `MAIL_FROM` | e.g. `Ohmward Careers <alerts@yourdomain.com>` |
5. **Pages:** Settings → Pages → Deploy from a branch → `main`, folder `/docs`.
6. **Start it:** Actions → scan → Run workflow. After that, each run starts the next one 15 minutes later.

## Running it

- `missing_companies.txt` lists favorites people asked for that no scanned board matches. Add their careers link to `companies.txt` (`python3 jobscan.py try <url>` tests one first).
- `python3 jobscan.py test` runs the self-check. `python3 jobscan.py dry` scans and prints matches without saving or emailing.
- After editing `docs/style.css` or `docs/common.js`, bump the `?v=` number where the four pages link them, or visitors' browsers keep showing the old file next to the new page.
- To preview the site locally, run `python3 -m http.server -d docs` and open http://localhost:8000.
- Free-tier limits: Resend sends 100 emails a day (3,000 a month). Daily-summary subscribers use one email a day each, and instant ones can use more. Upgrade the email plan before you get past roughly 50 subscribers.
