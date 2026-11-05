"""Download original Jira/JEditor attachments and attach them to the target issue."""
import hashlib
import html
import mimetypes
import os
import re
import ssl
import tempfile
from urllib.parse import parse_qs, quote, unquote, urljoin, urlsplit

UUID = re.compile(r'(?<![0-9a-f])[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}(?![0-9a-f])', re.I)


def decode_url(value):
    for _ in range(3):
        decoded = html.unescape(value)
        if decoded == value:
            break
        value = decoded
    return value.strip()


def origin(url):
    parsed = urlsplit(url)
    if parsed.scheme not in ('http', 'https') or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError('Attachment source must be an HTTP(S) URL without embedded credentials')
    return parsed.scheme.lower(), parsed.netloc.lower()


def image_dimensions(data):
    """Read common image dimensions without an extra imaging dependency."""
    import struct
    if data.startswith(b'\x89PNG\r\n\x1a\n') and len(data) >= 24:
        return struct.unpack('>II', data[16:24])
    if data[:6] in (b'GIF87a', b'GIF89a') and len(data) >= 10:
        return struct.unpack('<HH', data[6:10])
    if data.startswith(b'\xff\xd8'):
        offset = 2
        while offset + 4 <= len(data):
            if data[offset] != 255:
                offset += 1
                continue
            marker = data[offset + 1]
            offset += 2
            if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
                continue
            size = int.from_bytes(data[offset:offset + 2], 'big')
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF) and offset + 7 <= len(data):
                return (int.from_bytes(data[offset + 5:offset + 7], 'big'), int.from_bytes(data[offset + 3:offset + 5], 'big'))
            if size < 2:
                break
            offset += size
    return None


def source_configs():
    configs = {}
    for name in ('OPS', 'STG'):
        prefix = 'JIRA_DC_' + name
        url = os.environ.get(prefix + '_URL', '').rstrip('/')
        if not url:
            continue
        token = os.environ.get(prefix + '_API_TOKEN') or os.environ.get(prefix + '_TOKEN')
        user = os.environ.get(prefix + '_USER') or os.environ.get(prefix + '_ADMIN_EMAIL') or os.environ.get(prefix + '_EMAIL')
        password = os.environ.get(prefix + '_PASSWORD')
        cookie = os.environ.get(prefix + '_SESSION_COOKIE') or os.environ.get('JIRA_DC_SESSION_COOKIE') or os.environ.get('JIRA_DC_COOKIE')
        base_headers = {'User-Agent': 'JEditor-ADF-Migration/4.0'}
        candidates = []
        if token:
            candidates.append({'headers': {**base_headers, 'Authorization': 'Bearer ' + token}, 'auth': None})
        if cookie:
            candidates.append({'headers': {**base_headers, 'Cookie': cookie if '=' in cookie else 'JSESSIONID=' + cookie}, 'auth': None})
        if user and password:
            candidates.append({'headers': base_headers.copy(), 'auth': (user, password)})
        aliases = [value.strip().rstrip('/') for value in os.environ.get(prefix + '_URL_ALIASES', '').split(',') if value.strip()]
        for alias in aliases:
            origin(alias)
        configs[name] = {'url': url, 'candidates': candidates, 'aliases': aliases}
    return configs


class SourceClient:
    def __init__(self, configs, preferred='OPS', verify=True, session=None):
        self.configs, self.preferred, self.verify = configs, preferred, verify
        self._owned_session = False
        if session is not None:
            self.session = session
        else:
            import requests
            self.session = requests.Session()
            adapter = requests.adapters.HTTPAdapter(pool_connections=32, pool_maxsize=32)
            self.session.mount('https://', adapter)
            self.session.mount('http://', adapter)
            self._owned_session = True

    def close(self):
        if self._owned_session and self.session:
            self.session.close()

    def selected(self):
        if self.preferred not in self.configs:
            raise ValueError(f'Configure JIRA_DC_{self.preferred}_URL and source authentication for filename-only attachments')
        return self.configs[self.preferred]

    def config_for(self, url):
        host = origin(url)
        for config in self.configs.values():
            if origin(config['url']) == host:
                return config
        for config in self.configs.values():
            if host in [origin(alias) for alias in config.get('aliases', [])]:
                return config
        raise ValueError(f'Attachment source host is not configured: {urlsplit(url).hostname}; set its Jira DC URL/authentication')

    def get(self, url, *, binary=False):
        import requests
        initial_url = url
        initial_config = self.config_for(url)
        candidates = initial_config.get('candidates', [])
        if not candidates:
            raise ValueError('Source Jira authentication is missing (PAT/session cookie/username+password)')
        client = self.session or requests
        for candidate in candidates:
            url = initial_url
            config, authentication = initial_config, candidate
            for _ in range(6):
                try:
                    response = client.get(url, headers=authentication['headers'], auth=authentication['auth'], verify=self.verify,
                                          timeout=60, stream=binary, allow_redirects=False)
                except requests.exceptions.SSLError:
                    raise RuntimeError('Source Jira SSLError: 대화형 SSL 설정에서 사내 CA 또는 검증 생략을 선택하세요.') from None
                if response.status_code in (301, 302, 303, 307, 308):
                    location = response.headers.get('Location')
                    response.close()
                    if not location:
                        raise RuntimeError('Source download redirect has no Location')
                    destination = urljoin(url, location)
                    if re.search(r'/(?:login|signin|sso)(?:[./?]|$)', urlsplit(destination).path, re.I):
                        break  # This auth method was redirected to a login page; try the next one.
                    destination_config = self.config_for(destination)
                    if destination_config is not config:
                        if not destination_config.get('candidates'):
                            raise ValueError('Redirected source Jira has no configured authentication')
                        authentication = destination_config['candidates'][0]
                    config, url = destination_config, destination
                    continue
                if response.status_code in (401, 403) or response.headers.get('Content-Type', '').lower().startswith(('text/html', 'application/xhtml+xml')):
                    response.close()
                    break  # JEditor servlets sometimes require the explicitly configured cookie.
                if response.status_code != 200:
                    status = response.status_code
                    response.close()
                    raise RuntimeError(f'Source Jira GET failed: HTTP {status}')
                return response
            else:
                raise RuntimeError('Source Jira redirect limit exceeded')
        raise RuntimeError('Source Jira authentication failed or returned a login page (PAT/cookie/basic candidates exhausted)')

    def find_filename(self, issue_key, filename):
        base = self.selected()['url']
        response = self.get(base + '/rest/api/2/issue/' + quote(issue_key, safe='') + '?fields=attachment')
        try:
            matches = [a for a in response.json()['fields'].get('attachment', []) if a.get('filename') == filename]
        finally:
            response.close()
        if len(matches) != 1:
            raise ValueError(f'Original attachment filename lookup returned {len(matches)} matches: {filename}')
        url = matches[0].get('content')
        if not url:
            raise ValueError('Original attachment has no download URL')
        self.config_for(url)
        return url


class AttachmentMigrator:
    """Create one per issue under that issue's worker lock; cache across field rows."""
    def __init__(self, cloud, source, issue_key, existing, *, dry_run=True, source_issue_key=None):
        self.cloud, self.source, self.issue_key = cloud, source, issue_key
        self.source_issue_key = source_issue_key or issue_key
        self.existing = list(existing)
        self.dry_run, self.cache, self.media_cache = dry_run, {}, {}
        self.actions = []

    def _filename(self, reference):
        parsed = urlsplit(reference)
        if parsed.scheme or reference.startswith('/'):
            query = parse_qs(parsed.query)
            filename = (query.get('fileName') or query.get('filename') or query.get('name') or [''])[0]
            filename = filename or unquote(parsed.path.rsplit('/', 1)[-1])
        else:
            filename = unquote(reference)
        if not filename or filename in ('.', '..') or '/' in filename or '\\' in filename or re.search(r'[\x00-\x1f\x7f]', filename):
            raise ValueError('Attachment filename is missing or invalid')
        return filename

    def _resolve_uuid(self, attachment):
        attachment_id = str(attachment['id'])
        if attachment_id not in self.media_cache:
            media_id = None
            for _ in range(3):
                response = self.cloud.request('GET', '/rest/api/3/attachment/content/' + quote(attachment_id, safe=''),
                    allow_redirects=False, stream=True, accepted_statuses=(302, 303, 307))
                try:
                    # The media UUID is not the numeric Jira attachment ID.
                    location = response.headers.get('Location', '')
                    path_match = re.search(r'/file/(' + UUID.pattern + r')(?:/|$)', urlsplit(location).path, re.I)
                    query_id = (parse_qs(urlsplit(location).query).get('fileId') or [''])[0]
                    match = UUID.fullmatch(path_match.group(1) if path_match else query_id)
                    if match:
                        media_id = match.group(0)
                        break
                finally:
                    response.close()
            if not media_id:
                raise RuntimeError(f'Cannot resolve Cloud media UUID for attachment {attachment_id}; field was not saved')
            self.media_cache[attachment_id] = media_id
        return self.media_cache[attachment_id]

    def _upload(self, reference, filename):
        url = reference
        if not urlsplit(reference).scheme:
            url = urljoin(self.source.selected()['url'] + '/', reference) if reference.startswith('/') else self.source.find_filename(self.source_issue_key, filename)
        print(f'[DOWNLOAD] {self.source_issue_key} → {self.issue_key}: {filename}', flush=True)
        response = self.source.get(url, binary=True)
        with tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode='w+b') as file:
            digest, size = hashlib.sha256(), 0
            try:
                content_type = response.headers.get('Content-Type', '').split(';')[0].lower()
                if content_type in ('text/html', 'application/xhtml+xml'):
                    raise RuntimeError('Source download returned HTML/login page instead of attachment bytes')
                for chunk in response.iter_content(64 * 1024):
                    if chunk:
                        file.write(chunk)
                        digest.update(chunk)
                        size += len(chunk)
                if size == 0:
                    raise RuntimeError('Source attachment download was empty')
            finally:
                response.close()
            file.seek(0)
            sample = file.read(1024 * 1024)
            dimensions = image_dimensions(sample)
            prefix = sample[:256].lstrip().lower()
            if prefix.startswith((b'<!doctype html', b'<html', b'<form')):
                raise RuntimeError('Source attachment payload is a login/error HTML page')
            file.seek(0)
            # A multipart POST is never retried automatically after ambiguous failures.
            print(f'[UPLOAD] {self.issue_key}: {filename} ({size:,} bytes)', flush=True)
            uploaded = self.cloud.request('POST', '/rest/api/3/issue/' + quote(self.issue_key, safe='') + '/attachments',
                headers={'X-Atlassian-Token': 'no-check'},
                files={'file': (filename, file, content_type or mimetypes.guess_type(filename)[0] or 'application/octet-stream')}).json()
        if not isinstance(uploaded, list) or len(uploaded) != 1 or not uploaded[0].get('id'):
            raise RuntimeError('Unexpected Cloud attachment upload response')
        if dimensions:
            uploaded[0]['_source_dimensions'] = dimensions
        self.existing.append(uploaded[0])
        self.actions.append({'action': 'uploaded', 'filename': filename, 'id': str(uploaded[0]['id']),
                             'bytes': size, 'sha256': digest.hexdigest()})
        return uploaded[0]

    def __call__(self, reference, *, image=True, width=None, height=None):
        reference = decode_url(reference)
        filename = self._filename(reference)
        if self.dry_run:
            self.actions.append({'action': 'planned', 'filename': filename, 'image': image, 'source': reference})
            return {'type': 'paragraph', 'content': [{'type': 'text', 'text': f'[Attachment migration preview: {filename}]'}]}
        attachment = self.cache.get(reference)
        if attachment is None:
            matches = [item for item in self.existing if item.get('filename') == filename]
            if len(matches) > 1:
                raise ValueError(f'Cloud contains multiple attachments with the same filename: {filename}; cannot choose safely')
            if matches:
                attachment = matches[0]
                self.actions.append({'action': 'reused', 'filename': filename, 'id': str(attachment['id'])})
            else:
                attachment = self._upload(reference, filename)
            self.cache[reference] = attachment
        if not image:
            return {'type': 'paragraph', 'content': [{'type': 'text', 'text': filename, 'marks': [
                {'type': 'link', 'attrs': {'href': self.cloud.base_url + '/rest/api/3/attachment/content/' + quote(str(attachment['id']), safe='')}}]}]}
        attrs = {'type': 'file', 'id': self._resolve_uuid(attachment), 'collection': '', 'alt': filename}
        # ADF mediaSingle requires dimensions. Keep explicit source dimensions when present.
        dimensions = attachment.get('_source_dimensions') or (640, 480)
        if len(dimensions) != 2 or any(type(value) is not int or value <= 0 for value in dimensions):
            dimensions = (640, 480)
        w = int(width) if str(width).isdigit() and int(width) > 0 else None
        h = int(height) if str(height).isdigit() and int(height) > 0 else None
        attrs['width'] = w or (max(1, round(h * dimensions[0] / dimensions[1])) if h else dimensions[0])
        attrs['height'] = h or (max(1, round(w * dimensions[1] / dimensions[0])) if w else dimensions[1])
        return {'type': 'mediaSingle', 'attrs': {'layout': 'center'}, 'content': [{'type': 'media', 'attrs': attrs}]}
