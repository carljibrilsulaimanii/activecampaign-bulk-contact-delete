# ─────────────────────────────────────────────────────────────────────────────
# ac_delete_all_contacts.py
# Author:  Jibril Sulaiman
# What:    Deletes every contact in an ActiveCampaign account through the v3 API,
#          one DELETE per contact, staying under ActiveCampaign's rate limit.
#          Each round does a fresh fetch of the remaining contact ids (skipping ids
#          already logged as deleted), deletes them, and repeats until the server's
#          meta.total reads 0.
# Why:     The v3 API has no bulk-delete endpoint, and the rate limit (10 requests,
#          retry-after 1s) rejects anything faster than about one request per
#          second. Multi-threaded scripts get 429s within a few hundred requests.
#          Progress is logged per contact so a Ctrl+C or a crash loses nothing.
# How:     pip install requests
#          set AC_API_URL and AC_API_KEY (see README, Step 2), then from the folder
#          where you want the log files:
#              python scripts/ac_delete_all_contacts.py
#          Type DELETE ALL when asked. Re-run the same command to resume.
# ─────────────────────────────────────────────────────────────────────────────

import requests
import time
import os
import json

# ─────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────
API_URL = os.environ.get("AC_API_URL", "").strip().rstrip("/")   # e.g. https://youraccount.api-us1.com
API_KEY = os.environ.get("AC_API_KEY", "").strip()

if not API_URL or not API_KEY:
    raise SystemExit(
        "Set AC_API_URL and AC_API_KEY first (ActiveCampaign: Settings -> Developer; wording may differ).\n"
        "  PowerShell:  $env:AC_API_URL = \"https://youraccount.api-us1.com\"\n"
        "               $env:AC_API_KEY = \"<your API key>\"\n"
        "  macOS/Linux: export AC_API_URL=https://youraccount.api-us1.com AC_API_KEY=<your API key>"
    )
# ─────────────────────────────────────────────

HEADERS = {
    "Api-Token": API_KEY,
    "Content-Type": "application/json"
}

FETCH_DELAY  = 1.5   # 1.5s between fetch pages (safe under 10 req/window)
DELETE_DELAY = 1.2   # 1.2s between deletes (just over 1s retry-after)
MAX_RETRIES  = 10

FETCH_LOG  = "ac_fetched_ids.json"
DELETE_LOG = "ac_deleted_ids.txt"
FAILED_LOG = "ac_failed_ids.txt"


# ─────────────────────────────────────────────
# CORE REQUEST
# ─────────────────────────────────────────────

def request_with_retry(url, method="GET"):
    for attempt in range(MAX_RETRIES):
        try:
            response = requests.request(method, url, headers=HEADERS, timeout=30)
        except requests.exceptions.RequestException as e:
            print(f"  Connection error: {e}. Waiting 15s...")
            time.sleep(15)
            continue

        # 404 on DELETE = already gone, return immediately
        if response.status_code == 404 and method == "DELETE":
            return response

        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            wait = int(retry_after) + 1 if retry_after else 30
            print(f"  429 — waiting {wait}s (retry {attempt + 1}/{MAX_RETRIES})...")
            time.sleep(wait)
            continue

        return response

    print(f"  ❌ Gave up on {url} after {MAX_RETRIES} retries.")
    return None


# ─────────────────────────────────────────────
# FETCH — always does a fresh full fetch
# ─────────────────────────────────────────────

def load_deleted_ids():
    if os.path.exists(DELETE_LOG):
        with open(DELETE_LOG, "r") as f:
            return set(line.strip() for line in f if line.strip())
    return set()


def fetch_all_ids():
    """Fetches ALL current contact IDs from AC, excluding already-deleted ones."""
    print("\nFetching current contact list from AC...")
    already_deleted = load_deleted_ids()
    contact_ids = []
    limit = 100
    offset = 0

    while True:
        url = f"{API_URL}/api/3/contacts?limit={limit}&offset={offset}"
        response = request_with_retry(url)

        if response is None:
            print(f"  Fetch failed at offset {offset}. Stopping.")
            break

        if response.status_code != 200:
            print(f"  Error {response.status_code} at offset {offset}: {response.text}")
            break

        contacts = response.json().get("contacts", [])

        if not contacts:
            break

        for c in contacts:
            cid = str(c["id"])
            if cid not in already_deleted:
                contact_ids.append(cid)

        offset += limit

        if len(contact_ids) % 5000 == 0 and len(contact_ids) > 0:
            print(f"  {len(contact_ids):,} new IDs fetched so far...")

        time.sleep(FETCH_DELAY)

    print(f"  ✅ Fetch complete — {len(contact_ids):,} contacts to delete this round.")
    return contact_ids


# ─────────────────────────────────────────────
# DELETE
# ─────────────────────────────────────────────

def log_deleted(cid):
    with open(DELETE_LOG, "a") as f:
        f.write(f"{cid}\n")


def log_failed(cid, status):
    with open(FAILED_LOG, "a") as f:
        f.write(f"{cid},{status}\n")


def delete_batch(contact_ids):
    total = len(contact_ids)
    if total == 0:
        return 0

    print(f"\nDeleting {total:,} contacts...")
    print(f"  ETA: ~{round(total * DELETE_DELAY / 3600, 1)} hours at {DELETE_DELAY}s/delete")
    print(f"  Safe to Ctrl+C — progress saved per contact.\n")

    success = 0
    not_found = 0
    failed = 0

    for i, cid in enumerate(contact_ids, 1):
        response = request_with_retry(
            f"{API_URL}/api/3/contacts/{cid}", method="DELETE"
        )

        status = response.status_code if response else 0

        if status in (200, 204):
            success += 1
            log_deleted(cid)
        elif status == 404:
            not_found += 1
            log_deleted(cid)  # log as done, never retry
        else:
            failed += 1
            log_failed(cid, status)
            if failed <= 10:
                print(f"  ✗ Failed: {cid} (status {status})")

        if i % 500 == 0 or i == total:
            pct = round(i / total * 100, 1)
            eta = round((total - i) * DELETE_DELAY / 3600, 1)
            print(
                f"  [{pct}%] {i:,}/{total:,} — "
                f"✅ {success:,} deleted | "
                f"⬜ {not_found:,} already gone | "
                f"✗ {failed} failed | "
                f"ETA ~{eta}h"
            )

        time.sleep(DELETE_DELAY)

    print(f"\n  Round complete: {success:,} deleted | {not_found:,} already gone | {failed} failed")
    return success + not_found  # return total processed


# ─────────────────────────────────────────────
# MAIN LOOP — keeps going until AC shows 0
# ─────────────────────────────────────────────

def get_server_total():
    """Quick check of how many contacts AC reports."""
    response = request_with_retry(f"{API_URL}/api/3/contacts?limit=1")
    if response and response.status_code == 200:
        return int(response.json().get("meta", {}).get("total", -1))
    return -1


if __name__ == "__main__":
    print("=" * 55)
    print("  ActiveCampaign Bulk Contact Deleter")
    print("=" * 55)

    # Check current total
    total = get_server_total()
    if total == 0:
        print("✅ No contacts found on server. Nothing to do.")
    elif total == -1:
        print("❌ Could not reach AC API. Check your connection.")
    else:
        print(f"\nServer reports {total:,} contacts.")
        confirm = input(
            f"\n⚠️  PERMANENTLY delete ALL {total:,} contacts?\n"
            f"    Type 'DELETE ALL' to confirm: "
        )

        if confirm.strip() != "DELETE ALL":
            print("Aborted.")
        else:
            round_num = 0
            while True:
                round_num += 1
                print(f"\n{'=' * 55}")
                print(f"  ROUND {round_num}")
                print(f"{'=' * 55}")

                # Fresh fetch of remaining contacts
                ids = fetch_all_ids()

                if not ids:
                    # Double-check server total
                    server_total = get_server_total()
                    if server_total == 0:
                        print("\n🎉 All contacts deleted! Server confirms 0 contacts.")
                        break
                    elif server_total > 0:
                        print(f"\n  Server still shows {server_total:,} — fetching again...")
                        time.sleep(5)
                        continue
                    else:
                        print("\n  Could not verify server total. Stopping.")
                        break

                delete_batch(ids)

                # Check if more remain
                server_total = get_server_total()
                print(f"\n  Server now reports: {server_total:,} contacts remaining.")

                if server_total == 0:
                    print("🎉 All done!")
                    break
                elif server_total > 0:
                    print(f"  {server_total:,} still remaining (may include new imports). Starting next round...")
                    time.sleep(10)
                else:
                    print("  Could not verify. Stopping to be safe.")
                    break

            # Cleanup offer
            cleanup = input("\nRemove log files? (yes/no): ")
            if cleanup.lower() == "yes":
                for f in [DELETE_LOG, FAILED_LOG]:
                    if os.path.exists(f):
                        os.remove(f)
                print("Logs removed.")