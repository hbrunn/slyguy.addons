import json
import socket
import shutil
import re
import ssl
import os
from gzip import GzipFile

import requests
import urllib3
import six
from urllib3.util.connection import allowed_gai_family, _set_socket_options
from urllib3.exceptions import LocationParseError, ConnectTimeoutError, NewConnectionError
from urllib3.connection import HTTPSConnection, HTTPConnection
from urllib3.connectionpool import HTTPSConnectionPool
from socket import error as SocketError
from socket import timeout as SocketTimeout
from six import BytesIO
from six.moves.urllib_parse import urlparse
from kodi_six import xbmc
import dns.resolver

from . import userdata, settings, signals, mem_cache
from .util import get_kodi_proxy
from .smart_urls import get_dns_rewrites
from .log import log
from .language import _
from .exceptions import SessionError, Error
from .constants import DEFAULT_USERAGENT, CHUNK_SIZE, KODI_VERSION

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# KODI 17.6/18.9: OpenSSL 1.0.2j  26 Sep 2016
# KODI 19.5: OpenSSL 1.1.1d  10 Sep 2019
# KODI 20.0: OpenSSL 1.1.1q  5 Jul 2022
log.debug(ssl.OPENSSL_VERSION)

DEFAULT_HEADERS = {
    'User-Agent': DEFAULT_USERAGENT,
}

SSL_CIPHERS = 'ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-ECDSA-CHACHA20-POLY1305:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-RSA-AES256-GCM-SHA384:ECDHE-RSA-CHACHA20-POLY1305:ECDHE-ECDSA-AES128-SHA:ECDHE-ECDSA-AES256-SHA:ECDHE-RSA-AES128-SHA:ECDHE-RSA-AES256-SHA:AES128-GCM-SHA256:AES256-GCM-SHA384:AES128-SHA:AES256-SHA'
SSL_OPTIONS = ssl.OP_NO_SSLv2 | ssl.OP_NO_SSLv3 | ssl.OP_NO_COMPRESSION
DNS_CACHE = dns.resolver.Cache()


def json_override(func, error_msg):
    try:
        return func()
    except Exception as e:
        raise SessionError(error_msg or _.JSON_ERROR)


OPEN_SESSIONS = []
@signals.on(signals.AFTER_DISPATCH)
def close_sessions():
    for session in OPEN_SESSIONS:
        session.close()


class DOHResolver(object):
    def __init__(self, adapter, nameservers=None):
        self._adapter = adapter
        self.nameservers = nameservers or []
        self._session = RawSession()

    def query(self, host):
        class DNSResultWrapper(object):
            def __init__(self, answer):
                self.answer = answer

            def to_text(self):
                return self.answer

        for server in self.nameservers:
            key = (server, host)
            ips = mem_cache.get(key)

            if not ips:
                headers = {'accept': 'application/dns-json'}

                server_host = urlparse(server).netloc.lower()
                info = self._adapter.getaddrinfo(server_host, 443 if server.lower().startswith('https') else 80)
                families = [x[0] for x in info]

                params = {'name': host, 'dns': host}

                # prefer IPV4
                if socket.AF_INET in families or socket.AF_INET6 not in families:
                    params['type'] = 'A'
                else:
                    params['type'] = 'AAAA'

                log.debug("DOH Request: {} for {} type {}".format(server, host, params['type']))

                try:
                    data = self._session.get(server, params=params, headers=headers).json()
                except Exception as e:
                    log.debug(e)
                    continue

                suitable = [x for x in data['Answer'] if x['type'] in (1, 28)] #ipv4 or ipv6
                ttl = min([x['TTL'] for x in suitable])
                ips = [x['data'] for x in suitable]
                mem_cache.set(key, ips, expires=ttl)

            if ips:
                return [DNSResultWrapper(ip) for ip in ips]

        raise SessionError('Unable to resolve host: {} with nameservers: {}'.format(host, self.nameservers))


class RequestsDoHHTTPConnection(HTTPConnection):
    def __init__(self, *args, **kw):
        self.adapter = kw.pop('adapter')
        super(RequestsDoHHTTPConnection, self).__init__(*args, **kw)

    def create_connection(self, address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, source_address=None, socket_options=None):
        host, port = address
        if host.startswith("["):
            host = host.strip("[]")
        err = None

        family = allowed_gai_family()

        try:
            host.encode("idna")
        except UnicodeError:
            return six.raise_from(
                LocationParseError(u"'%s', label empty or too long" % host), None
            )

        for res in self.adapter.getaddrinfo(host, port, family, socket.SOCK_STREAM):
            af, socktype, proto, canonname, sa = res
            sock = None
            try:
                sock = socket.socket(af, socktype, proto)

                # If provided, set socket level options before connecting.
                _set_socket_options(sock, socket_options)

                if timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:
                    sock.settimeout(timeout)
                if source_address:
                    sock.bind(source_address)
                sock.connect(sa)
                return sock

            except socket.error as e:
                err = e
                if sock is not None:
                    sock.close()
                    sock = None

        if err is not None:
            raise err

        raise socket.error("getaddrinfo returns an empty list")

    # This code is copied from urllib3/connection.py version 1.26.8 (from requests v2.28.1)
    def _new_conn(self):
        extra_kw = {}
        if self.source_address:
            extra_kw["source_address"] = self.source_address

        if self.socket_options:
            extra_kw["socket_options"] = self.socket_options

        try:
            conn = self.create_connection((self._dns_host, self.port), self.timeout, **extra_kw)
        except SocketTimeout:
            raise ConnectTimeoutError(
                self,
                "Connection to %s timed out. (connect timeout=%s)"
                % (self.host, self.timeout),
            )
        except SocketError as e:
            raise NewConnectionError(
                self, "Failed to establish a new connection: %s" % e
            )

        return conn


class RequestsDoHHTTPSConnection(RequestsDoHHTTPConnection, HTTPSConnection):
    def connect(self, *args, **kwargs):
        retval = super(RequestsDoHHTTPSConnection, self).connect(*args, **kwargs)
        self.adapter.on_connect(self)
        return retval


class SessionAdapter(requests.adapters.HTTPAdapter):
    def __init__(self):
        self.session_data = {}
        self._context_cache = {}
        super(SessionAdapter, self).__init__()

    def on_connect(self, https_connection):
        log.debug('SSL Cipher: {} - {}'.format(https_connection.sock.server_hostname, https_connection.sock.cipher()))

    def connection_from_pool_key(self, pool_key, request_context):
        if self.session_data['ssl_ciphers'] or self.session_data['ssl_options']:
            context_key = (self.session_data['ssl_ciphers'], self.session_data['ssl_options'])
            request_context['ssl_context'] = self._context_cache[context_key] = self._context_cache.get(context_key, requests.packages.urllib3.util.ssl_.create_urllib3_context(ciphers=SSL_CIPHERS, options=SSL_OPTIONS))
            pool_key = pool_key._replace(key_ssl_context=context_key)

        if self.session_data['interface_ip']:
            request_context['source_address'] = (self.session_data['interface_ip'], 0)
            pool_key = pool_key._replace(key_source_address=request_context['source_address'])

        # ensure we get a unique pool (socket) for same domain on different rewrite ips
        if self.session_data['rewrite'] and self.session_data['rewrite'][0] == request_context['host']:
            pool_key = pool_key._replace(key_server_hostname=self.session_data['rewrite'][1])
        # ensure we get a unique pool (socket) for same domain on different resolvers
        elif self.session_data['resolver'] and self.session_data['resolver'][0] == request_context['host']:
            pool_key = pool_key._replace(key_server_hostname=self.session_data['resolver'][1].nameservers[0])

        request_context['adapter'] = self
        return self.poolmanager._connection_from_pool_key(pool_key, request_context)

    def getaddrinfo(self, host, port, family=0, _type=0):
        orig_host = host

        if self.session_data['rewrite'] and self.session_data['rewrite'][0] == host:
            host = self.session_data['rewrite'][1]
            log.debug("DNS Rewrite: {} -> {}".format(orig_host, host))

        elif self.session_data['resolver'] and self.session_data['resolver'][0] == host:
            try:
                host = self.session_data['resolver'][1].query(host)[0].to_text()
                log.debug('DNS Resolver: {} -> {} -> {}'.format(orig_host, self.session_data['resolver'][1].nameservers[0], host))
            except Exception as e:
                log.exception(e)
                log.error('Failed to resolve. Falling back to dns lookup')
                host = orig_host

        if ':' in host:
            first, second = (family, None)
        else:
            first = socket.AF_INET
            second = socket.AF_INET6 if family == socket.AF_UNSPEC else None

        try:
            addresses = socket.getaddrinfo(host, port, first, _type)
        except socket.gaierror:
            if second:
                addresses = socket.getaddrinfo(host, port, second, _type)
            else:
                raise

        if addresses[0][4][0] != host:
            log.debug('DNS Resolver: {} -> {}'.format(host, addresses[0][4][0]))

        return addresses

    def init_poolmanager(self, *args, **kwargs):
        super(SessionAdapter, self).init_poolmanager(*args, **kwargs)
        self.poolmanager._connection_from_pool_key = self.poolmanager.connection_from_pool_key
        self.poolmanager.connection_from_pool_key = self.connection_from_pool_key

    def get_connection(self, url, proxies=None):
        conn = super(SessionAdapter, self).get_connection(url, proxies)
        if isinstance(conn, HTTPSConnectionPool):
            conn.ConnectionCls = RequestsDoHHTTPSConnection
        else:
            # HTTP type
            conn.ConnectionCls = RequestsDoHHTTPConnection
        return conn


class RawSession(requests.Session):
    def __init__(self, verify=None, timeout=None, auto_close=True, ssl_ciphers=SSL_CIPHERS, ssl_options=SSL_OPTIONS, proxy=None):
        super(RawSession, self).__init__()
        self._verify = verify
        self._timeout = timeout
        self._rewrites = []
        self._session_cache = {}
        self._proxy = proxy
        self._cert = None
        self._ssl_ciphers = ssl_ciphers
        self._ssl_options = ssl_options

        if auto_close:
            OPEN_SESSIONS.append(self)

        self._adapter = SessionAdapter()
        for prefix in ('http://', 'https://'):
            self.mount(prefix, self._adapter)

    def set_dns_rewrites(self, rewrites):
        for entries in rewrites:
            pattern = entries.pop()
            pattern = re.escape(pattern).replace('\*', '.*')
            pattern = re.compile(pattern, flags=re.IGNORECASE)

            new_entries = []
            for entry in entries:
                _type = 'skip'
                if entry.startswith('p:'):
                    _type = 'proxy'
                    entry = entry[2:]
                elif entry.startswith('r:'):
                    _type = 'resolver'
                    entry = entry[2:]
                elif entry.startswith('i:'):
                    _type = 'interface_ip'
                    entry = entry[2:]
                elif entry[0].isdigit():
                    _type = 'dns'
                else:
                    _type = 'url_sub'
                new_entries.append([_type, entry])

            self._rewrites.append([pattern, sorted(new_entries, key=lambda x: x[0] == 'dns')])

    def set_cert(self, cert):
        self._cert = cert
        if cert:
            log.debug('SSL CERT SET TO: {}'.format(cert))

    def _get_cert(self):
        if not self._cert:
            return None

        if self._cert.lower().startswith('http'):
            url = self._cert
            self._cert = None

            log.debug('Downloading cert: {}'.format(url))
            resp = self.request('get', url, stream=True)

            self._cert = xbmc.translatePath('special://temp/temp.pem')
            with open(self._cert, 'wb') as f:
                shutil.copyfileobj(resp.raw, f)

        return xbmc.translatePath(self._cert)

    def set_proxy(self, proxy):
        self._proxy = proxy

    def _get_proxy(self):
        if self._proxy and self._proxy.lower().strip() == 'kodi':
            self._proxy = get_kodi_proxy()
        return self._proxy

    def close(self):
        super(RawSession, self).close()
        if self in OPEN_SESSIONS:
            OPEN_SESSIONS.remove(self)

    def __del__(self):
        self.close()

    def request(self, method, url, **kwargs):
        req = requests.Request(method, url, params=kwargs.pop('params', None))
        url = req.prepare().url

        session_data = {
            'ssl_ciphers': self._ssl_ciphers,
            'ssl_options': self._ssl_options,
            'proxy': None,
            'interface_ip': None,
            'rewrite': None,
            'resolver': None,
            'url': url,
        }

        if url in self._session_cache:
            session_data = self._session_cache[url]
        elif self._rewrites:
            for row in self._rewrites:
                if not row[0].search(url):
                    continue

                for entry in row[1]:
                    if entry[0] == 'skip':
                        continue
                    if entry[0] == 'url_sub':
                        session_data['url'] = re.sub(row[0], entry[1], url, count=1)
                    elif entry[0] == 'proxy':
                        session_data['proxy'] = entry[1]
                    elif entry[0] == 'interface_ip':
                        session_data['interface_ip'] = entry[1]
                    elif entry[0] == 'dns':
                        session_data['rewrite'] = [urlparse(session_data['url']).netloc.lower(), entry[1]]
                    elif entry[0] == 'resolver' and entry[1]:
                        if entry[1].lower().startswith('http'):
                            resolver = DOHResolver(self._adapter)
                        else:
                            resolver = dns.resolver.Resolver(configure=False)
                            resolver.cache = DNS_CACHE

                        resolver.nameservers = [entry[1],]
                        session_data['resolver'] = [urlparse(session_data['url']).netloc.lower(), resolver]
                break

            self._session_cache[url] = session_data

        self._adapter.session_data = session_data

        if session_data['url'] != url:
            log.debug("URL Changed: {}".format(session_data['url']))

        if session_data['proxy'] is None:
            session_data['proxy'] = self._get_proxy()

        if session_data['proxy']:
            # remove username, password from proxy for logging
            parsed = urlparse(session_data['proxy'])
            replaced = parsed._replace(netloc="{}:{}@{}".format('username', 'password', parsed.hostname) if parsed.username else parsed.hostname)
            log.debug("Proxy: {}:{}".format(replaced.geturl(), parsed.port))

            kwargs['proxies'] = {
                'http': session_data['proxy'],
                'https': session_data['proxy'],
            }

        if self._cert:
            if KODI_VERSION > 18:
                # @SECLEVEL added in OpenSSL 1.1.1
                session_data['ssl_ciphers'] += '@SECLEVEL=0'
            kwargs['verify'] = False
            kwargs['cert'] = self._get_cert()

        if 'verify' not in kwargs:
            kwargs['verify'] = self._verify

        if 'timeout' not in kwargs:
            kwargs['timeout'] = self._timeout

        try:
            # Do request
            result = super(RawSession, self).request(method, session_data['url'], **kwargs)
        except requests.exceptions.ConnectionError as e:
            log.exception(e)
            if session_data['proxy']:
                raise SessionError(_(_.CONNECTION_ERROR_PROXY, host=urlparse(session_data['url']).netloc.lower()))
            else:
                raise SessionError(_(_.CONNECTION_ERROR, host=urlparse(session_data['url']).netloc.lower()))

        return result

class Session(RawSession):
    def __init__(self, headers=None, cookies_key=None, base_url='{}', timeout=None, attempts=None, verify=None, dns_rewrites=None, auto_close=True, return_json=False, **kwargs):
        super(Session, self).__init__(verify=settings.common_settings.getBool('verify_ssl', True) if verify is None else verify,
            timeout=settings.common_settings.getInt('http_timeout', 30) if timeout is None else timeout, auto_close=auto_close, **kwargs)

        self._headers = headers or {}
        self._cookies_key = cookies_key
        self._base_url = base_url
        self._attempts = settings.common_settings.getInt('http_retries', 1) if attempts is None else attempts
        self._return_json = return_json
        self.before_request = None
        self.after_request = None

        self.set_dns_rewrites(get_dns_rewrites() if dns_rewrites is None else dns_rewrites)
        self.set_proxy(settings.get('proxy_server') or settings.common_settings.get('proxy_server'))

        self.headers.update(DEFAULT_HEADERS)
        self.headers.update(self._headers)

        if self._cookies_key:
            self.cookies.update(userdata.get(self._cookies_key, {}))

    def gz_json(self, *args, **kwargs):
        kwargs['return_json'] = False
        resp = self.get(*args, **kwargs)
        json_text = GzipFile(fileobj=BytesIO(resp.content)).read()
        return json.loads(json_text)

    def request(self, method, url, timeout=None, attempts=None, verify=None, error_msg=None, retry_not_ok=False, retry_delay=1000, log_url=None, return_json=None, **kwargs):
        method = method.upper()

        if not url.startswith('http'):
            url = self._base_url.format(url)

        attempts = max(self._attempts if attempts is None else attempts, 1)
        return_json = self._return_json if return_json is None else return_json

        if timeout is not None:
            kwargs['timeout'] = timeout

        if verify is not None:
            kwargs['verify'] = verify

        for i in range(1, attempts+1):
            attempt = 'Attempt {}/{}: '.format(i, attempts)
            if i > 1 and retry_delay:
                xbmc.sleep(retry_delay)

            if self.before_request:
                self.before_request()

            log.debug('{}{} {}'.format(attempt, method, log_url or url))

            try:
                resp = super(Session, self).request(method, url, **kwargs)
            except SessionError:
                if i == attempts:
                    raise
                else:
                    continue
            except Exception as e:
                #log.exception(e) #causes log spam in service loop when no internet
                raise SessionError(error_msg or _.NO_RESPONSE_ERROR)

            if retry_not_ok and not resp.ok:
                continue

            if return_json:
                try:
                    data = resp.json()
                except:
                    if i == attempts:
                        raise
                    else:
                        continue

            break

        resp.json = lambda func=resp.json, error_msg=error_msg: json_override(func, error_msg)

        if self.after_request:
            self.after_request(resp)

        if return_json:
            return data
        else:
            return resp

    def save_cookies(self):
        if not self._cookies_key:
            raise Exception('A cookies key needs to be set to save cookies')

        userdata.set(self._cookies_key, self.cookies.get_dict())

    def clear_cookies(self):
        if self._cookies_key:
            userdata.delete(self._cookies_key)
        self.cookies.clear()

    def chunked_dl(self, url, dst_path, method='GET', **kwargs):
        kwargs['stream'] = True
        kwargs['return_json'] = False
        resp = self.request(method, url, **kwargs)
        resp.raise_for_status()

        with open(dst_path, 'wb') as f:
            for chunk in resp.iter_content(CHUNK_SIZE):
                f.write(chunk)

        return resp

def gdrivedl(url, dst_path):
    ID_PATTERNS = [
        re.compile('/file/d/([0-9A-Za-z_-]{10,})(?:/|$)', re.IGNORECASE),
        re.compile('id=([0-9A-Za-z_-]{10,})(?:&|$)', re.IGNORECASE),
        re.compile('([0-9A-Za-z_-]{10,})', re.IGNORECASE)
    ]
    FILE_URL = "https://drive.usercontent.google.com/download?uc-download-link=Download%20anyway&id={id}&confirm={confirm}&uuid={uuid}"
    CONFIRM_PATTERNS = [
        re.compile(r"confirm=([0-9A-Za-z_-]+)", re.IGNORECASE),
        re.compile(r"name=\"confirm\"\s+value=\"([0-9A-Za-z_-]+)\"", re.IGNORECASE),
    ]
    UUID_PATTERN = re.compile(r"name=\"uuid\"\s+value=\"([0-9A-Za-z_-]+)\"", re.IGNORECASE)
    FILENAME_PATTERN = re.compile('filename="(.*?)"', re.IGNORECASE)

    id = None
    for pattern in ID_PATTERNS:
        match = pattern.search(url)
        if match:
            id = match.group(1)
            break

    if not id:
        raise Error('No Gdrive file ID found in url')

    with Session() as session:
        resp = session.get(FILE_URL.format(id=id, confirm='', uuid=''), stream=True)
        if not resp.ok:
            raise Error('Gdrive url no longer exists')

        if 'ServiceLogin' in resp.url:
            raise Error('Gdrive url does not have link sharing enabled')

        content_disposition = resp.headers.get("content-disposition")
        if not content_disposition:
            html = resp.read()

            for pattern in CONFIRM_PATTERNS:
                confirm = pattern.search(html)
                if confirm: break

            uuid = UUID_PATTERN.search(html)
            if uuid:
                uuid = uuid.group(1)
            else:
                uuid=''

            if confirm:
                resp = session.get(FILE_URL.format(id=id, confirm=confirm.group(1), uuid=uuid), stream=True)
            elif b"Google Drive - Quota exceeded" in html:
                raise Error("Quota exceeded for this file")
            else:
                log.debug("Trying confirmation 't' as a last resort")
                resp = session.get(FILE_URL.format(id=id, confirm='t', uuid=uuid), stream=True)

        filename = FILENAME_PATTERN.search(content_disposition).group(1)
        dst_path = dst_path if os.path.isabs(dst_path) else os.path.join(dst_path, filename)

        resp.raise_for_status()
        with open(dst_path, 'wb') as f:
            for chunk in resp.iter_content(CHUNK_SIZE):
                f.write(chunk)

    return filename
