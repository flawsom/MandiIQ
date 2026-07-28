with open(r'C:\Users\sibap\Downloads\MandiIQ\mandi_rdd\api\main.py', 'r', encoding='utf-8') as f:
    content = f.read()

old = '''    return {
        "status": "ok",
        "message": "Database restored from R2 backup.",
        "bytes_downloaded": len(compressed),
        "bytes_decompressed": len(decompressed),
        "db_path": str(DB_PATH),
    }


@app.post("/admin/reset-metrics", tags=["Admin"])'''

new = '''    return {
        "status": "ok",
        "message": "Database restored from R2 backup.",
        "bytes_downloaded": len(compressed),
        "bytes_decompressed": len(decompressed),
        "db_path": str(DB_PATH),
    }


@app.post("/admin/backup-to-r2", tags=["Admin"])
async def admin_backup_to_r2():
    """Upload the current DuckDB database to Cloudflare R2 as a gzipped backup.
    Reads the local DuckDB file, compresses it, and uploads to R2 as
    mandi_iq.duckdb.gz. Requires R2 credentials configured as environment variables.
    Returns:
        dict with status, message, bytes uploaded, and compression ratio.
    """
    try:
        from mandi_rdd.storage.duckdb_store import DB_PATH
        import gzip
        import urllib.request
        import base64
        import hmac
        import hashlib
        import datetime

        if not DB_PATH.exists():
            return {"status": "error", "message": f"Database file not found: {DB_PATH}"}

        # Read and compress
        raw = DB_PATH.read_bytes()
        compressed = gzip.compress(raw, compresslevel=6)

        # R2 credentials
        bucket = os.environ.get("R2_BUCKET") or os.environ.get("R2_BUCKET_NAME") or ""
        account_id = os.environ.get("R2_ACCOUNT_ID") or ""
        access_key = os.environ.get("R2_ACCESS_KEY_ID") or ""
        secret_key = os.environ.get("R2_SECRET_ACCESS_KEY") or ""

        if not all([bucket, account_id, access_key, secret_key]):
            missing = [k for k, v in [
                ("R2_BUCKET/R2_BUCKET_NAME", bucket), ("R2_ACCOUNT_ID", account_id),
                ("R2_ACCESS_KEY_ID", access_key), ("R2_SECRET_ACCESS_KEY", secret_key),
            ] if not v]
            return {"status": "error", "message": "Missing R2 credentials: " + ", ".join(missing)}

        # Build S3 request
        endpoint = f"https://{account_id}.r2.cloudflarestorage.com"
        key = "mandi_iq.duckdb.gz"
        url = f"{endpoint}/{bucket}/{key}"

        # AWS SigV4 signing
        now = datetime.datetime.utcnow()
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        date_stamp = now.strftime("%Y%m%d")
        region = "auto"
        service = "s3"
        algorithm = "AWS4-HMAC-SHA256"
        credential_scope = f"{date_stamp}/{region}/{service}/aws4_request"
        credential = f"{access_key}/{credential_scope}"

        signed_headers = "host;x-amz-content-sha256;x-amz-date"
        content_sha256 = hashlib.sha256(compressed).hexdigest()

        canonical_request = (
            "PUT\n"
            f"/{bucket}/{key}\n"
            "\n"
            f"host:{account_id}.r2.cloudflarestorage.com\n"
            f"x-amz-content-sha256:{content_sha256}\n"
            f"x-amz-date:{amz_date}\n"
            "\n"
            f"{signed_headers}\n"
            f"{content_sha256}"
        )

        string_to_sign = (
            f"{algorithm}\n"
            f"{amz_date}\n"
            f"{credential_scope}\n"
            f"{hashlib.sha256(canonical_request.encode()).hexdigest()}"
        )

        def sign(key, msg):
            return hmac.new(key, msg.encode(), hashlib.sha256).digest()

        k_date = sign(("AWS4" + secret_key).encode(), date_stamp)
        k_region = sign(k_date, region)
        k_service = sign(k_region, service)
        k_signing = sign(k_service, "aws4_request")
        signature = hmac.new(k_signing, string_to_sign.encode(), hashlib.sha256).hexdigest()

        auth_header = (
            f"{algorithm} Credential={credential}, SignedHeaders={signed_headers}, Signature={signature}"
        )

        headers = {
            "Host": f"{account_id}.r2.cloudflarestorage.com",
            "X-Amz-Content-Sha256": content_sha256,
            "X-Amz-Date": amz_date,
            "Authorization": auth_header,
            "Content-Type": "application/gzip",
        }

        req = urllib.request.Request(url, data=compressed, headers=headers, method="PUT")
        with urllib.request.urlopen(req, timeout=120) as resp:
            resp.read()

        logger.info("R2 backup: uploaded %d bytes (compressed from %d) to s3://%s/%s",
                    len(compressed), len(raw), bucket, key)

        return {
            "status": "ok",
            "message": "Database backed up to R2.",
            "bytes_uploaded": len(compressed),
            "bytes_original": len(raw),
            "compression_pct": round(100 * (1 - len(compressed) / len(raw)), 1),
            "r2_key": key,
        }

    except urllib.error.HTTPError as e:
        return {"status": "error", "message": "R2 upload failed (HTTP " + str(e.code) + "): " + e.reason}
    except (TimeoutError, urllib.error.URLError, OSError) as e:
        return {"status": "error", "message": "R2 upload failed: " + str(e)}
    except Exception as e:
        return {"status": "error", "message": "Backup failed: " + str(e)}


@app.post("/admin/reset-metrics", tags=["Admin"])'''

if old in content:
    content = content.replace(old, new)
    with open(r'C:\Users\sibap\Downloads\MandiIQ\mandi_rdd\api\main.py', 'w', encoding='utf-8') as f:
        f.write(content)
    print('Replaced successfully')
else:
    print('Pattern not found')