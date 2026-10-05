"""Cloudflare R2 (S3 API) for the drops bucket.

boto3 is imported lazily inside functions, like rasterio in raster_tiles.py:
the site must run without it, and a missing or broken wheel should break
only the drops pages. The bucket is PRIVATE — everything the browser touches
is a presigned URL minted here, scoped to one key and a short lifetime.

Config comes from the environment (settings.DROPS_R2_*). The lidar bucket's
token is scoped to that bucket alone, so this needs its own token.
"""
from django.conf import settings

_client = None


def configured():
    return all((settings.DROPS_R2_ENDPOINT, settings.DROPS_R2_ACCESS_KEY_ID,
                settings.DROPS_R2_SECRET_ACCESS_KEY, settings.DROPS_R2_BUCKET))


def bucket():
    return settings.DROPS_R2_BUCKET


def client():
    global _client
    if _client is None:
        import boto3
        from botocore.config import Config
        _client = boto3.client(
            's3',
            endpoint_url=settings.DROPS_R2_ENDPOINT,
            aws_access_key_id=settings.DROPS_R2_ACCESS_KEY_ID,
            aws_secret_access_key=settings.DROPS_R2_SECRET_ACCESS_KEY,
            region_name='auto',
            config=Config(
                signature_version='s3v4',
                # botocore >= 1.36 adds CRC checksums to every request by
                # default; R2 rejects them on presigned uploads the browser
                # performs without the matching header. Only when asked.
                request_checksum_calculation='when_required',
                response_checksum_validation='when_required',
                retries={'max_attempts': 4},
            ),
        )
    return _client


# ---- single objects --------------------------------------------------------

def presign_put(key, expires=3600):
    """URL the browser PUTs one whole object to. The content type is not
    signed, so whatever the browser sends is stored."""
    return client().generate_presigned_url(
        'put_object', Params={'Bucket': bucket(), 'Key': key}, ExpiresIn=expires)


def presign_get(key, expires=7200, filename=None, inline=True):
    params = {'Bucket': bucket(), 'Key': key}
    if filename:
        disp = 'inline' if inline else 'attachment'
        params['ResponseContentDisposition'] = f'{disp}; filename="{filename}"'
    return client().generate_presigned_url('get_object', Params=params, ExpiresIn=expires)


def head(key):
    """{'size', 'etag', 'content_type'} or None when the object is absent."""
    from botocore.exceptions import ClientError
    try:
        r = client().head_object(Bucket=bucket(), Key=key)
    except ClientError as e:
        if e.response.get('Error', {}).get('Code') in ('404', 'NoSuchKey', 'NotFound'):
            return None
        raise
    return {'size': r['ContentLength'], 'etag': r.get('ETag', '').strip('"'),
            'content_type': r.get('ContentType', '')}


def get_bytes(key):
    return client().get_object(Bucket=bucket(), Key=key)['Body'].read()


def put_bytes(key, data, content_type):
    client().put_object(Bucket=bucket(), Key=key, Body=data, ContentType=content_type)


def delete(key):
    client().delete_object(Bucket=bucket(), Key=key)


# ---- multipart (files over the single-PUT threshold) -----------------------

def mp_create(key, content_type):
    r = client().create_multipart_upload(Bucket=bucket(), Key=key,
                                         ContentType=content_type or 'application/octet-stream')
    return r['UploadId']


def mp_sign_part(key, upload_id, part_number, expires=3600):
    return client().generate_presigned_url(
        'upload_part',
        Params={'Bucket': bucket(), 'Key': key, 'UploadId': upload_id,
                'PartNumber': int(part_number)},
        ExpiresIn=expires)


def mp_list_parts(key, upload_id):
    parts, marker = [], 0
    while True:
        r = client().list_parts(Bucket=bucket(), Key=key, UploadId=upload_id,
                                PartNumberMarker=marker)
        for p in r.get('Parts', []):
            parts.append({'PartNumber': p['PartNumber'], 'Size': p['Size'],
                          'ETag': p['ETag']})
        if not r.get('IsTruncated'):
            return parts
        marker = r['NextPartNumberMarker']


def mp_complete(key, upload_id, parts):
    client().complete_multipart_upload(
        Bucket=bucket(), Key=key, UploadId=upload_id,
        MultipartUpload={'Parts': [{'ETag': p['ETag'], 'PartNumber': int(p['PartNumber'])}
                                   for p in sorted(parts, key=lambda p: int(p['PartNumber']))]})


def mp_abort(key, upload_id):
    client().abort_multipart_upload(Bucket=bucket(), Key=key, UploadId=upload_id)


# ---- bucket setup ----------------------------------------------------------

def cors_rules(origins):
    """The browser PUTs to the bucket directly, so the bucket must name the
    site as an allowed origin and expose ETag (multipart completion needs
    each part's ETag, which a browser cannot read unless exposed)."""
    return {'CORSRules': [{
        'AllowedOrigins': list(origins),
        'AllowedMethods': ['GET', 'PUT', 'HEAD'],
        'AllowedHeaders': ['*'],
        'ExposeHeaders': ['ETag'],
        'MaxAgeSeconds': 3600,
    }]}


def put_cors(origins):
    client().put_bucket_cors(Bucket=bucket(), CORSConfiguration=cors_rules(origins))
