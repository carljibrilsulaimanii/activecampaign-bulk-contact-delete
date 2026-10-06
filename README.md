<!--
  README.md -- ActiveCampaign bulk contact delete
  Author:  Jibril Sulaiman
  Date:    2026-10-06
  What:    Step-by-step guide for deleting every contact in an ActiveCampaign account with a
           Python script that calls the v3 API one contact at a time, survives the rate
           limit, resumes from log files, and loops until the server reports 0 contacts.
  Why:     ActiveCampaign's v3 API has no bulk-delete endpoint and a tight rate limit, so
           the obvious scripts fail within a few hundred requests. At large scale even the
           working script is too slow, and a support ticket is the route that finishes it.
-->

# ActiveCampaign: delete every contact through the API

A Python script that deletes every contact in an ActiveCampaign account through the v3
API. It stays under ActiveCampaign's rate limit, writes every result to a log file so you
can stop and restart it at any time, and keeps going in rounds until the server says the
account has 0 contacts.

**Read this first:** the script runs at about one delete per second. That's fine for a few
thousand contacts, slow for tens of thousands, and not realistic for hundreds of thousands.
In the account this was built for (~600K contacts), the script would have needed about
**200 hours** of nonstop running. **What actually finished the job was a support ticket to
ActiveCampaign asking for a server-side wipe.** Step 1 helps you decide which route is
yours.

## Why it exists

ActiveCampaign doesn't give you a quick way to empty an account through the API:

1. **There is no bulk-delete endpoint in the v3 API.** Deleting a contact is one
   `DELETE /api/3/contacts/{id}` call per contact. There's no "delete these 100 ids" call
   and no "delete everything" call.
2. **Deleting a list doesn't delete its contacts.** `DELETE /api/3/lists/{id}` removes the
   list. The contacts stay in the account.
3. **The rate limit is tight.** A single delete test returned these response headers:
   `ratelimit-limit: 10`, `ratelimit-remaining: 0`, `retry-after: 1`. Anything faster
   than about one request per second gets `429 Too Many Requests`.
4. **Pushing harder makes it worse.** Hammering through 429s can get your machine's IP
   address throttled, at which point calls fail for hours and nothing gets deleted.

What went wrong in the build, in order:

- A first version used 5 parallel threads. The contact fetch hit a 429 and stopped after
  200 contacts, out of ~600K.
- Adding backoff and dropping to 3 threads still stalled. Fetching all the ids took many
  restarts.
- Going single-threaded at one request per second and honouring `Retry-After` worked, but
  the script's own estimate was ~165 hours at 1.0 s per delete, and ~200 hours at the
  safer 1.2 s it runs at now.
- Many deletes came back `404`. Those contacts were already gone, and retrying them
  wasted the rate limit. Now a 404 counts as done.
- The contact count in the ActiveCampaign screen didn't seem to drop. It lags. The API's
  `meta.total` is the number to trust.
- New contacts kept arriving while the script ran, so the total went *up*. The script now
  re-fetches every round and loops until the total is 0.
- The script then showed status `0` for hours. A test from a phone hotspot got a normal
  answer from the API, so the machine's IP had been throttled. Almost nothing had been
  deleted in that window.
- An account admin opened a support ticket with ActiveCampaign for a server-side wipe of
  all contacts. That's what emptied the account.

**So use the script for smaller accounts**, or as the pattern for any job that has to
make one ActiveCampaign API call per contact. **For hundreds of thousands of contacts,
open the support ticket first.**

## How it works

```text
 check meta.total ──► type DELETE ALL ──► ROUND 1
                                           │
     ┌─────────────────────────────────────┘
     ▼
 fetch every contact id, 100 per page, 1.5 s apart
 (skip ids already in ac_deleted_ids.txt)
     │
     ▼
 DELETE each id, 1.2 s apart
   200/204 or 404 ──► append to ac_deleted_ids.txt
   anything else  ──► append "id,status" to ac_failed_ids.txt
   429            ──► wait Retry-After + 1 s, retry (up to 10 times)
     │
     ▼
 check meta.total again ──► 0? done : next ROUND
```

## What's in this repo

| Path | What it is | Where it runs |
|---|---|---|
| [`README.md`](README.md) | This guide | — |
| [`scripts/`](scripts/) | The delete script (Step 6) | Your computer, Python 3.8+ |
| [`requirements.txt`](requirements.txt) | The one package the script needs, `requests` (Step 5) | `pip install` |
| [`.gitignore`](.gitignore) | Keeps the run logs out of git; they list real contact ids | — |

## Table of contents

- [1. Requirements](#1-requirements)
- [2. Setup, step by step](#2-setup-step-by-step)
  - [Step 1: Decide: script or support ticket](#step-1-decide-script-or-support-ticket)
  - [Step 2: Get your API URL and key](#step-2-get-your-api-url-and-key)
  - [Step 3: Test one delete in Postman](#step-3-test-one-delete-in-postman)
  - [Step 4: Stop new contacts from arriving](#step-4-stop-new-contacts-from-arriving)
  - [Step 5: Install Python and set your credentials](#step-5-install-python-and-set-your-credentials)
  - [Step 6: Run the script](#step-6-run-the-script)
  - [Step 7: Watch progress and check the real count](#step-7-watch-progress-and-check-the-real-count)
  - [Step 8: Stop, resume and clean the failed log](#step-8-stop-resume-and-clean-the-failed-log)
  - [Step 9: Finish](#step-9-finish)
- [The log files](#the-log-files)
- [Troubleshooting](#troubleshooting)
- [Limits](#limits)
- [Security](#security)

## 1. Requirements

| Need | Why |
|---|---|
| An ActiveCampaign user who can see **Settings → Developer** (wording may differ), usually an admin | That's where the API URL and key are |
| Python 3.8 or later, from https://www.python.org/downloads/ | Runs the script |
| The `requests` package | The script's only dependency |
| Postman, from https://www.postman.com/downloads/ (optional) | For the single-delete test in Step 3. PowerShell works too. |
| A computer that can stay on and awake for the whole run | A sleeping laptop stops the run. You can resume (Step 8), but it adds hours. |

> ⚠️ **This deletes contacts permanently.** Export anything you need first. Deleted
> contacts don't come back.

## 2. Setup, step by step

### Step 1: Decide: script or support ticket

About 5 minutes.

**1a.** Find your contact count. In ActiveCampaign, open **Contacts**. The number sits next
to the heading, as **Contacts (12345)**. It lags behind reality, but it's fine for this
decision.

**1b.** Estimate the script's run time. The script spends 1.2 seconds per delete and
1.5 seconds per page of 100 contacts it fetches, before any 429 waits:

| Contacts | Deletes alone | Plus fetching the ids | Realistic? |
|---|---|---|---|
| 1,000 | ~20 minutes | ~15 seconds | Yes |
| 10,000 | ~3.3 hours | ~2.5 minutes | Yes, run it overnight |
| 50,000 | ~17 hours | ~13 minutes | Possible, with resumes |
| 100,000 | ~33 hours | ~25 minutes | Painful. Consider the ticket. |
| ~600,000 | **~200 hours** (8+ days nonstop) | ~2.5 hours | No. Open the ticket. |

The formula is `contacts × 1.2 s` for deletes plus `contacts ÷ 100 × 1.5 s` for the fetch.
429 waits come on top.

**1c. Large account: open a support ticket.** Have an account admin contact ActiveCampaign
support and ask them to delete all contacts in the account server-side. Say plainly that
the v3 API has no bulk-delete endpoint and that one-by-one deletion would take days at the
rate limit. In the build, this is what emptied a ~600K-contact account. If you take this
route, you can stop here. Step 3 is still a cheap way to confirm your key works if you
want to delete a few test contacts yourself.

**1d. Small or medium account: use the script.** Carry on with Step 2.

> ⚠️ **Don't try to beat the limit with threads.** Parallel requests trip the 429 within a
> few hundred calls. Keep pushing and your IP can be throttled for hours (see
> Troubleshooting). One request at a time is the fastest way that actually finishes.

✅ **Check:** you know your contact count and which route you're taking.

### Step 2: Get your API URL and key

About 2 minutes.

**2a.** In ActiveCampaign, open **Settings → Developer** (wording may differ).

**2b.** Copy the two values on that page:

| On the page | What it looks like | Goes into |
|---|---|---|
| **URL** (wording may differ) | `https://youraccount.api-us1.com` | `AC_API_URL` (Step 5) |
| **Key** (wording may differ) | a long string of letters and numbers | `AC_API_KEY` (Step 5) |

> ⚠️ **Use the API URL, not the address in your browser bar.** The app runs on one
> address; the API has its own (`...api-us1.com` or similar). The script adds
> `/api/3/...` itself, so copy the URL without a trailing slash.

> ⚠️ **The key is a full-access credential.** It can delete everything, which is the
> point here. Don't paste it into a chat, a screenshot or a file you'll share. In the
> build, it ended up in several terminal screenshots.

✅ **Check:** you have both values, and the URL starts with `https://`.

### Step 3: Test one delete in Postman

About 5 minutes. This proves the key works and shows you the rate limit before you commit
to a long run.

**3a.** In Postman, create a new request: **GET**
`https://youraccount.api-us1.com/api/3/contacts?limit=1`

**3b.** On the request's **Headers** tab, add one row:

| Key | Value |
|---|---|
| `Api-Token` | your API key |

**3c.** Click **Send**. The body is JSON with a `contacts` array of one contact, and a
`meta` object whose `total` is the server's real contact count. Copy that contact's `id`.

**3d.** Create a second request: **DELETE**
`https://youraccount.api-us1.com/api/3/contacts/<id>` with the same `Api-Token` header.
Click **Send**.

Success looks like:

| Where | You see |
|---|---|
| Status | **200** |
| **Body** (JSON view) | `{}` |
| **Headers** | `ratelimit-limit` `10`, `ratelimit-remaining` (counts down; `0` when you're at the limit), `retry-after` `1` |

**3e.** Prefer PowerShell? The same test, with the values from Step 2 set as environment
variables first (Step 5b shows how):

```powershell
$headers = @{ "Api-Token" = $env:AC_API_KEY }
$r = Invoke-RestMethod -Uri "$env:AC_API_URL/api/3/contacts?limit=1" -Headers $headers
Write-Host "Total contacts on server: $($r.meta.total)"
$id = $r.contacts[0].id
Invoke-RestMethod -Method DELETE -Uri "$env:AC_API_URL/api/3/contacts/$id" -Headers $headers
```

> ⚠️ **Stop the script before any manual test.** The script uses the whole rate limit.
> While it runs, every Postman or PowerShell call you make gets `429 Too Many Requests`,
> and in PowerShell the follow-up line fails with *"Cannot index into a null array."*
> because the first call returned nothing. Press **Ctrl+C** in the script's window first.
> It resumes cleanly (Step 8).

✅ **Check:** the DELETE returned 200 with `{}`, and the contact count dropped by one.

### Step 4: Stop new contacts from arriving

About 10 minutes, depending on how many sources feed the account.

**4a.** Pause whatever adds contacts: scheduled imports, forms, and integrations or
automations that create contacts.

**4b.** If you can't pause them, that's fine. The script re-fetches every round and keeps
going until the total is 0. Expect extra rounds.

> ⚠️ **In the build, contacts kept arriving during the run** and the total went up between
> checks. A one-shot "fetch everything, then delete it" script finishes with contacts
> still in the account. That's why this one loops.

✅ **Check:** nothing new is scheduled to land in the account during the run.

### Step 5: Install Python and set your credentials

About 5 minutes.

**5a.** Install Python 3.8 or later, then open PowerShell in the repo folder and install
the one package from [`requirements.txt`](requirements.txt):

```powershell
pip install -r requirements.txt
```

**5b.** Set your API URL and key as environment variables in the **same** PowerShell
window you'll run the script from. Replace the placeholders with your values from Step 2:

```powershell
$env:AC_API_URL = "https://youraccount.api-us1.com"
$env:AC_API_KEY = "REPLACE_WITH_YOUR_API_KEY"
```

On macOS or Linux:

```bash
export AC_API_URL="https://youraccount.api-us1.com"
export AC_API_KEY="REPLACE_WITH_YOUR_API_KEY"
```

These are the only two inputs:

| Environment variable | Value | Read by |
|---|---|---|
| `AC_API_URL` | Your API URL from Step 2, no trailing slash | [`scripts/ac_delete_all_contacts.py`](scripts/ac_delete_all_contacts.py) |
| `AC_API_KEY` | Your API key from Step 2 | [`scripts/ac_delete_all_contacts.py`](scripts/ac_delete_all_contacts.py) |

> ⚠️ **`$env:` variables last only for that PowerShell window.** Open a new window to
> resume later and you have to set them again. If either one is missing, the script stops
> right away with *"Set AC_API_URL and AC_API_KEY first..."* and makes no API call.

✅ **Check:** `python -c "import requests"` prints nothing (no error), and
`echo $env:AC_API_URL` prints your URL.

### Step 6: Run the script

About 2 minutes to start. Then hours, depending on Step 1.

**6a.** Choose the folder the logs will live in. The script writes its log files to
whatever folder you run it **from**, and only reads them back from that same folder. Run
it from the repo folder every time.

**6b.** Run [`scripts/ac_delete_all_contacts.py`](scripts/ac_delete_all_contacts.py):

```powershell
python scripts/ac_delete_all_contacts.py
```

Runs [`scripts/ac_delete_all_contacts.py`](scripts/ac_delete_all_contacts.py).

**6c.** It first asks the server for the real count, then asks you to confirm:

```text
=======================================================
  ActiveCampaign Bulk Contact Deleter
=======================================================

Server reports 12,345 contacts.

⚠️  PERMANENTLY delete ALL 12,345 contacts?
    Type 'DELETE ALL' to confirm:
```

Type `DELETE ALL` exactly, in capitals, and press **Enter**. Anything else prints
`Aborted.` and deletes nothing.

**6d.** Round 1 starts with the fetch:

```text
=======================================================
  ROUND 1
=======================================================

Fetching current contact list from AC...
  429 — waiting 2s (retry 1/10)...
  ✅ Fetch complete — 12,345 contacts to delete this round.

Deleting 12,345 contacts...
  ETA: ~4.1 hours at 1.2s/delete
  Safe to Ctrl+C — progress saved per contact.
```

Occasional `429 — waiting 2s` lines are normal. That's the script reading `Retry-After: 1`
and adding a second.

The script's settings, if you need to change them (top of the file):

| Setting | Value | What it does |
|---|---|---|
| `FETCH_DELAY` | `1.5` | Seconds between pages of 100 ids during the fetch |
| `DELETE_DELAY` | `1.2` | Seconds between deletes. Just over the 1 s `retry-after`. |
| `MAX_RETRIES` | `10` | 429 retries per call before it gives up and logs status `0` |

> ⚠️ **Don't lower the delays.** The 429 retries cost more time than the delays save, and
> sustained 429s are how the IP got throttled in the build.

> ⚠️ **Stop the computer from sleeping** for the length of the run.

✅ **Check:** after the fetch, you see `Deleting N contacts...` with an ETA, and the first
progress line appears after 500 deletes (about 10 minutes).

### Step 7: Watch progress and check the real count

About 2 minutes per check.

**7a.** Every 500 deletes the script prints one line:

```text
  [4.1%] 500/12,345 — ✅ 497 deleted | ⬜ 3 already gone | ✗ 0 failed | ETA ~3.9h
```

| Field | Meaning |
|---|---|
| `✅ deleted` | DELETE returned 200 or 204 |
| `⬜ already gone` | DELETE returned 404. The contact no longer exists. Logged as done, never retried. |
| `✗ failed` | Anything else, including status `0` (gave up after 10 retries). The first 10 are printed as `✗ Failed: <id> (status <code>)`. |

**7b.** At the end of each round it asks the server for the real count:

```text
  Round complete: 12,300 deleted | 45 already gone | 0 failed

  Server now reports: 0 contacts remaining.
🎉 All done!
```

If the count isn't 0, it prints `... still remaining (may include new imports). Starting
next round...` and goes again.

**7c.** To check the count yourself, **stop the script first** (Ctrl+C, see Step 3), then
run the `meta.total` check from Step 3e:

```powershell
$headers = @{ "Api-Token" = $env:AC_API_KEY }
$r = Invoke-RestMethod -Uri "$env:AC_API_URL/api/3/contacts?limit=1" -Headers $headers
Write-Host "Total contacts on server: $($r.meta.total)"
```

> ⚠️ **The count in the ActiveCampaign screen lags.** In the build, the **Contacts** count
> didn't seem to move while the script was deleting. Trust `meta.total` from the API.

> ⚠️ **Many `already gone` results are normal.** A 404 on a delete means the contact no
> longer exists by the time the script reaches it. In the build, long runs of 404s
> appeared right after restarts. Retrying them only burns the rate limit, so the script
> logs them as done.

✅ **Check:** `meta.total` is lower than at the start of the run.

### Step 8: Stop, resume and clean the failed log

About 2 minutes.

**8a. Stop** any time with **Ctrl+C**. Every result is written to the log the moment it
comes back, so nothing is lost. You'll see a Python `KeyboardInterrupt` traceback. That's
expected.

**8b. Resume** by running the same command from the same folder (set the two environment
variables again if it's a new window, Step 5b):

```powershell
python scripts/ac_delete_all_contacts.py
```

Runs [`scripts/ac_delete_all_contacts.py`](scripts/ac_delete_all_contacts.py).

It confirms the count again (type `DELETE ALL`), re-fetches what's left, skips every id
in `ac_deleted_ids.txt`, and carries on.

**8c. The failed log, optional cleanup.** This version never reads `ac_failed_ids.txt`. It
only appends to it. Any failed id that still exists is picked up by the next round's
fetch on its own. Clean the file when it's grown and you've dealt with what's in it:

- If the ids in it are **confirmed gone** (for example a batch of 404s logged by an
  older run), move them into the deleted log and empty the failed log, in PowerShell:

  ```powershell
  Get-Content ac_failed_ids.txt | ForEach-Object { ($_ -split ",")[0] } | Add-Content ac_deleted_ids.txt
  Clear-Content ac_failed_ids.txt
  ```

  Each line in the failed log is `id,status`. The first command keeps only the id.

- Otherwise, just empty it:

  ```powershell
  Clear-Content ac_failed_ids.txt
  ```

> ⚠️ **Only move ids you know are gone.** Anything in `ac_deleted_ids.txt` is skipped by
> every future fetch. Move a contact that still exists and the script never deletes it:
> it ends up printing `Server still shows N — fetching again...` forever. The fix is in
> Troubleshooting.

✅ **Check:** after a resume, the round starts with `Fetching current contact list from
AC...`, and the number to delete is lower than last time.

### Step 9: Finish

About 2 minutes.

**9a.** When the server reports 0, you'll see one of:

```text
🎉 All contacts deleted! Server confirms 0 contacts.
```

or `🎉 All done!` after the last round.

**9b.** The script then asks:

```text
Remove log files? (yes/no):
```

Type `yes` to delete `ac_deleted_ids.txt` and `ac_failed_ids.txt`. Anything else keeps
them.

**9c.** Turn back on whatever you paused in Step 4.

✅ **Check:** the Step 3e `meta.total` check prints `Total contacts on server: 0`. The
ActiveCampaign **Contacts** screen catches up later.

## The log files

All three are written to the folder you run the script from.

| File | Contents | Read back by the script? |
|---|---|---|
| `ac_deleted_ids.txt` | One id per line: every contact that returned 200, 204 or 404 | **Yes.** Every fetch skips these ids. |
| `ac_failed_ids.txt` | `id,status` per line: everything else, including status `0` | No. Write-only, for your review (Step 8c). |
| `ac_fetched_ids.json` | Named in the script (`FETCH_LOG`) but not written by this version | — |

An earlier version saved the fetched ids to `ac_fetched_ids.json` and reused them. It was
dropped because new contacts arriving mid-run were missed. The current version re-fetches
every round.

## Troubleshooting

| You got | Cause | Fix |
|---|---|---|
| `Set AC_API_URL and AC_API_KEY first...` | An environment variable isn't set in this window | Step 5b |
| `❌ Could not reach AC API. Check your connection.` at start | The first count check failed: wrong URL or key, no network, or the IP is throttled | Check the URL and key with Step 3. If those are right, see the throttling row. |
| `429 Too Many Requests` on every manual Postman or PowerShell call | The script is running and using the whole limit | Ctrl+C the script, then test (Step 3) |
| *"Cannot index into a null array."* in PowerShell | The `?limit=1` call before it returned 429, so there was no contact to read | Stop the script and run both lines again |
| `Failed: <id> (status 0)` for hours, the count not dropping | Your IP is throttled. In the build, almost nothing had been deleted during these hours. | Stop the script. Run the Step 3e check from a **different network** (a phone hotspot worked in the build). If it answers there, your IP is throttled: wait it out, or run from the other network. |
| Long runs of `404` / `already gone` | The contact no longer existed when the script reached it | Normal. They're logged as done and never retried. |
| The ActiveCampaign **Contacts** count doesn't drop | The screen lags behind the API | Trust `meta.total` (Step 7c) |
| The total goes **up** between checks | Contacts are still being imported or created | Step 4. The script keeps looping until 0. |
| `Server still shows N — fetching again...` repeating with nothing deleted | Every remaining contact's id is in `ac_deleted_ids.txt` (often after the Step 8c move), or the server count is lagging | Wait one or two loops. If it doesn't clear, Ctrl+C, rename `ac_deleted_ids.txt`, and run again. Contacts that are really gone won't be fetched; a few may come back as 404s, which is harmless. |
| `Could not verify. Stopping to be safe.` | The count check after a round failed | Re-run the script (Step 8b). It resumes. |
| Fetch stops early: `Fetch failed at offset N. Stopping.` | 10 429s in a row on one page | Re-run (Step 8b). If it repeats, see the throttling row. |
| A `NameError` or crash from an edited copy | A missing import or a partial paste | Use the complete file from the repo, not a fragment |
| It will take days | The rate limit and no bulk endpoint | Step 1c: the support ticket |

## Limits

- **About one delete per second, no faster.** There's no bulk endpoint to batch them. Use
  the Step 1 table before you start.
- **Deletes everything.** There's no filter. To delete only some contacts, the fetch would
  need to pull ids from a filtered source instead of all contacts. That isn't part of this
  script.
- **One machine, one IP.** Splitting the work across several machines on different
  networks was suggested in the build, not tried.
- **The ActiveCampaign screen also offers select-all and delete.** It was suggested in the
  build as an alternative and not tested here, so how far it scales is unknown.
- **Contacts only.** Lists, tags, custom fields, automations and deals are not touched.

## Security

- The API key can read and delete everything in the account. Treat it like a password.
  Keep it in the environment variable, never in the script file.
- Screenshots of a terminal or Postman often show the key. Check before you share one.
- The log files contain your contact ids. [`.gitignore`](.gitignore) keeps them out of
  git; delete them when you're done (Step 9b).
- If the key leaks, generate a new one in **Settings → Developer** (wording may differ)
  and check that the old one stops working.

---

Built by [Jibril Sulaiman](https://github.com/carljibrilsulaimanii).
