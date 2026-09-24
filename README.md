# Real estate job watch

Checks the careers pages of the firms in `firms.yaml` every morning, keeps the
roles that match your filters, and publishes a private-link page where you can
save, hide and mark roles as applied. Optionally emails you each day's new roles.

## How it works

Most managers post jobs through a hiring system (Workday, Greenhouse, Lever,
SmartRecruiters, Workable, Teamtailor, Personio, Recruitee). The scanner finds
which one sits behind each careers page and reads its job feed directly, which
is far more reliable than reading the web page. Pages it cannot identify get a
simple scan for links that look like job postings.

GitHub runs the scan for free once a day and hosts the page.

## Setup (about 15 minutes, once)

1. Create a free account at github.com and a new **public** repository, for
   example `jobwatch`. (Private repositories need a paid plan to host the page.
   The page only lists public job adverts.)
2. On the repository page choose **Add file, Upload files** and drag in the
   whole contents of this folder, including the `.github` folder. On a Mac,
   press Cmd+Shift+. in Finder to see it.
3. **Settings, Pages**: Source "Deploy from a branch", branch `main`, folder
   `/docs`. Save. Your page address appears at the top after the first run.
4. **Actions** tab: enable workflows, open "Daily job scan", press
   **Run workflow**. It takes a few minutes.
5. Open the page, expand **Sources checked on the last run** at the bottom and
   fix any firm that shows a problem or 0 listed (see below).

### Daily email (optional)

1. In your Google account create an App Password (Security, 2-Step
   Verification, App passwords).
2. In the repository: **Settings, Secrets and variables, Actions**. Add secrets
   `SMTP_USER` (your Gmail address), `SMTP_PASS` (the app password) and
   `DIGEST_TO` (where to send it). Under the Variables tab add `SITE_URL` with
   your page address.

No email is sent on days with nothing new.

## LinkedIn job alerts

LinkedIn is not scraped. Instead the tool reads the job alert emails LinkedIn
sends you, from your own Gmail.

1. On LinkedIn, search Jobs for what you want (for example "real estate asset
   management", location London) and switch on **Set alert**. Choose daily
   emails. Repeat for each search you want.
2. Make sure the alerts arrive at the Gmail address in the `SMTP_USER` secret.
   If they go to another address, set up forwarding to that Gmail.
3. Add the `SMTP_USER` and `SMTP_PASS` secrets as in "Daily email" above. The
   LinkedIn step uses the same app password.

LinkedIn roles show on the page marked "via LinkedIn". A role already found on
the firm's own careers page is not listed twice. Since alerts only announce new
roles, a LinkedIn role is treated as open for 30 days. Turn this off with
`enabled: false` under `linkedin_alerts` in `firms.yaml`.

## Recruiters

Recruiter job pages are listed under `recruiters:` in `firms.yaml` and work the
same way as firms. Their adverts usually hide the client, so they show on the
page marked "(recruiter)". Add the agencies your own recruiter contacts work
for, one line each.

## No repeated roles

Each role is identified by firm plus a cleaned-up title, not by its link. So
these all show as one entry: the same advert with a new tracking link, a role
taken down and reposted, the same role posted for several locations, and a role
found on both the firm's site and LinkedIn. The daily email only includes roles
never seen before, and your saved or applied marks stay on a role even if its
link changes.

Recruiter adverts rarely name the client, so they cannot be matched for
certain. When a recruiter role closely matches a role on a firm's own site, the
page shows "Possibly the same role as..." so you can check before applying
twice.

## Contacts

For each new role the tool opens the advert once and picks out a named
contact, email and phone where the advert gives them. Recruiter adverts
usually name the consultant; firm adverts rarely do. Where there is no contact,
the page links to a LinkedIn people search for that firm, which you open while
logged in. For LinkedIn roles, check "Meet the hiring team" on the advert.

Your page is public, so it only shows contact details that were already public
in the advert.

## Fixing a firm

On the firm's careers site, click through to a single job advert and copy its
address. If it is on one of the systems above, add it as `ats_url`:

```yaml
- {name: Heitman, careers_url: "https://www.heitman.com/careers", ats_url: "https://boards.greenhouse.io/heitman"}
```

If the jobs only appear after the page loads, add `browser: true`. If the URL
itself is wrong, update `careers_url`.

## Adding firms and changing filters

Add a line under `firms:` in `firms.yaml`. Use `broad: true` for multi-asset
managers so only real estate roles come through. Edit the keyword lists at the
top of the file to widen or narrow what is kept. Commit the change and the next
run uses it.

## Testing on your own computer

```
pip install -r requirements.txt
python -m playwright install chromium
python -m jobwatch.run --detect          # which system each firm uses
python -m jobwatch.run --firm Heitman    # everything found, with KEEP marks
python -m jobwatch.run                   # full run, writes docs/
```

## Good to know

- Saved, applied and hidden marks are stored in the browser you use, so they
  do not sync between phone and laptop.
- LinkedIn and job boards are not scraped. Their terms prohibit it and they
  block automated access.
- The link scan fallback can pick up the odd news headline with a job-like
  title. Hide it once and it stays hidden.
- Roles that disappear from a firm's site are marked closed and kept for 60
  days so applied roles stay visible.
