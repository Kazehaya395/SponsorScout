"""One run-local detail budget/cache shared by support, location and title reads.

No network activity at construction. Failure caches are run-local (not a
permanent verdict about a host). Memory is bounded; cache hits spend no budget.
"""
from __future__ import annotations

import threading
import time
from collections import Counter, OrderedDict
from urllib.parse import urlsplit


class DetailRequestState:
    def __init__(self,cache_size=512):
        self.lock = threading.RLock()
        self.gates = [threading.RLock() for _ in range(64)]
        self.results = OrderedDict()
        self.metadata = OrderedDict()
        self.failed = set()
        self.browser_attempted = set()
        self.owners = {}
        self.memberships = {}
        self.outcomes = {}
        self.scopes = {}
        self.total = 0
        self.cache_size = max(16,int(cache_size))

    def gate(self,url):
        return self.gates[hash(url)%len(self.gates)]

    def register(self,url,scope):
        if url and scope:
            with self.lock:
                self.owners.setdefault(url,scope)
                self.memberships.setdefault(url,set()).add(scope)

    def owner(self,url):
        return self.owners.get(url) or urlsplit(url).netloc.lower()

    def _scope(self,url):
        key = self.owner(url)
        return self.scopes.setdefault(key,{'started':None,'requests':0,'browser':0,'counts':Counter(),'stops':set()})

    def count(self,url,kind):
        with self.lock:
            self._scope(url)['counts'][kind] += 1

    def get(self,url):
        with self.lock:
            result = self.results.get(url)
            if result is not None:
                self.results.move_to_end(url)
            return result

    def get_metadata(self,url):
        with self.lock:
            metadata = self.metadata.get(url)
            if metadata is not None:
                self.metadata.move_to_end(url)
            return metadata

    def put(self,url,result,metadata=None):
        with self.lock:
            old = self.results.get(url)
            # Do not replace a full JD with a shell from a later phase.
            if old is None or len(str(result[0] or ''))>=len(str(old[0] or '')):
                self.results[url] = tuple(result)
                self.results.move_to_end(url)
            if metadata is not None:
                self.metadata[url] = metadata
                self.metadata.move_to_end(url)
            for cache in (self.results,self.metadata):
                while len(cache)>self.cache_size:
                    cache.popitem(last=False)

    def failure(self,url,blocked=False,reason="",**evidence):
        with self.lock:
            self.failed.add(url)
            self._scope(url)['counts']['blocked' if blocked else 'failed'] += 1
            self.observe(url,kind='blocked' if blocked else 'failed',reason=reason,**evidence)
            self.put(url,('','',''))

    def allowed(self,url,config,browser=False,*,retry=False):
        with self.lock:
            if url in self.failed or (browser and url in self.browser_attempted and not retry):
                return False
            scope = self._scope(url)
            reason = ''
            if scope['requests']>=config.MAX_DETAIL_SCAN_PER_COMPANY:
                reason = 'per-target request cap'
            elif self.total>=config.MAX_DETAIL_SCAN_TOTAL and scope['requests']>=config.DETAIL_MIN_PER_COMPANY:
                reason = 'global request cap (reserved floor used)'
            elif scope['started'] is not None and time.monotonic()-scope['started']>=config.DETAIL_SCAN_TIME_BUDGET_SEC:
                reason = 'shared detail time budget exhausted'
            elif browser and scope['browser']>=getattr(config,'DETAIL_BROWSER_FALLBACK_PER_COMPANY',12):
                reason = 'shared browser fallback cap'
            if reason:
                scope['stops'].add(reason)
                scope['counts']['budget_skipped'] += 1
                return False
            return True

    def reserve(self,url,config,browser=False,*,retry=False):
        with self.lock:
            if not self.allowed(url,config,browser=browser,retry=retry):
                return False
            scope = self._scope(url)
            if scope['started'] is None:
                scope['started'] = time.monotonic()
            scope['requests'] += 1
            self.total += 1
            scope['counts']['browser' if browser else 'http'] += 1
            if browser:
                scope['browser'] += 1
                self.browser_attempted.add(url)
            return True

    def summary(self,scope):
        with self.lock:
            data = self.scopes.get(scope)
            if data is None:
                return 'detail activity: no additional requests (saved/provider text only)'
            counts = data['counts']
            stats = '; '.join(f'{name}={counts.get(name,0)}' for name in
                              ('http','browser','reused','fetched','failed','blocked','budget_skipped'))
            stops = ', '.join(sorted(data['stops'])) or 'none'
            return f'detail activity: {stats}; stop={stops}'


    def observe(self,url,text='',*,kind='',reason='',transport='',status=0,title=''):
        """Keep semantic outcomes separately from request/cache counters.

        Outcomes are run-local and bounded by this run's admitted detail URLs.
        A later genuinely readable response resolves a previous challenge;
        cache reuse alone does not fabricate resolution or spend a request.
        """
        with self.lock:
            old=self.outcomes.get(url,{})
            length=len(str(text or '').strip())
            kind=kind or ('readable' if length>=200 else 'thin' if length else 'empty')
            if old.get('kind')=='readable' and kind in ('thin','empty'):
                return
            self.outcomes[url]={'url':url,'kind':kind,'reason':str(reason or '')[:800],
                'transport':str(transport),'http_status':int(status or 0),
                'title':str(title or '')[:180],
                'jd_chars':max(length,int(old.get('jd_chars') or 0))}

    def scope_blocked(self,scope):
        with self.lock:
            return [dict(value) for url,value in self.outcomes.items()
                    if scope in self.memberships.get(url,()) and value.get('kind')=='blocked']

    def coverage(self,scope,urls=(),jd_lookup=None):
        with self.lock:
            selected=set(urls) or {url for url,scopes in self.memberships.items() if scope in scopes}
            result=Counter(observed=len(selected),readable=0,thin=0,empty=0,failed=0,blocked=0,unattempted=0)
            for url in selected:
                value=self.outcomes.get(url,{})
                saved=str(jd_lookup(url) or '') if callable(jd_lookup) else ''
                if len(saved.strip())>=200 or value.get('kind')=='readable':
                    result['readable']+=1
                elif value.get('kind') in ('thin','empty','failed','blocked'):
                    result[value['kind']]+=1
                else:
                    result['unattempted']+=1
            return dict(result)

    def coverage_summary(self,scope,urls=(),jd_lookup=None):
        data=self.coverage(scope,urls,jd_lookup)
        return 'JD coverage: '+ '; '.join(f'{key}={value}' for key,value in data.items())

    # ── FIX P19c (2026-10-28): transport-level visibility ──────────────────
    # A retry used to leave no trace at all. _response_failure() records an
    # outcome only for statuses that look like a wall, and observe() lets a
    # later readable response outrank an earlier 429, so run 20261028T192816
    # logged its two slowest seeds (Philips 2,959.8 s, Prysmian 2,897.3 s --
    # 72.6% of the whole run) as "failed=0; blocked=0; Attempts=1". Nothing
    # below changes a verdict or a counter that any reader already consumes:
    # these are extra names on the same diagnostics line, printed only when
    # they are non-zero, so a healthy seed's text is byte-identical.
    def note_transport(self, scope, kind, amount=1):
        """Count a transport/detail event against a seed's scope."""
        if not scope or not kind:
            return
        with self.lock:
            data = self.scopes.setdefault(
                scope,
                {'started': None, 'requests': 0, 'browser': 0,
                 'counts': Counter(), 'stops': set()})
            data['counts'][kind] += amount

    #: names shown by transport_summary(), in order
    TRANSPORT_KEYS = ('retries', 'throttled_429', 'server_5xx',
                      'network_errors', 'detail_errors', 'no_id',
                      'empty_listing_item', 'dropped_no_id',
                      'skipped_external_url', 'no_container',
                      'budget_skipped')

    def transport_summary(self, scope):
        """Return 'transport: ...' for this scope, or '' when all zero."""
        with self.lock:
            counts = (self.scopes.get(scope) or {}).get('counts') or {}
            parts = [f'{name}={counts[name]}' for name in self.TRANSPORT_KEYS
                     if counts.get(name)]
            backoff = counts.get('backoff_seconds')
            if backoff:
                position = 1 if counts.get('retries') else 0
                parts.insert(position, f'backoff={round(float(backoff), 1)}s')
            if not parts:
                return ''
            return 'transport: ' + ' '.join(parts)


    def scope_samples(self,scope,limit=4):
        with self.lock:
            samples=[]
            for url,value in self.outcomes.items():
                if scope not in self.memberships.get(url,()) or value.get('kind') not in ('empty','thin','failed','blocked'):
                    continue
                samples.append('response evidence: '+ '; '.join(f'{key}={value.get(key,"")}' for key in
                    ('url','kind','transport','http_status','jd_chars','title','reason')))
                if len(samples)>=limit:break
            return samples
