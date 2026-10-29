"""Bounded, session-cookie-aware job-country reads for ambiguous place names.

No geocoder, probes, extra browser or title/company/HQ inference. Explicit
JobPosting.jobLocation.address.addressCountry is authoritative; ambiguous or
multi-country payloads remain Unknown. Detail budgets/cache are run-local.
"""
from __future__ import annotations
import json,re,time
from collections import Counter
from html import unescape
from sponsorscout.core.location_country import country_from_location,COUNTRY_NAMES,ISO2_TO_COUNTRY
from sponsorscout.core.place_index import place_country_candidates,explicit_country_name
from sponsorscout.scanning.response_evidence import challenge_reason,response_metadata


def structured_job_country(html, iso3=None):
    iso3=iso3 or {}
    countries=set()
    def canonical(value):
        if isinstance(value,dict):value=value.get('name')or value.get('alternateName')or''
        if not isinstance(value,str):return ''
        value=unescape(value).strip()
        return COUNTRY_NAMES.get(value.casefold())or ISO2_TO_COUNTRY.get(value.casefold())or iso3.get(value.upper(),'')or explicit_country_name(value,allow_code=True)
    def inspect(item):
        if isinstance(item,list):
            for child in item[:32]:inspect(child)
        elif isinstance(item,dict):
            if item.get('@graph'):inspect(item['@graph'])
            kind=item.get('@type')
            if kind=='JobPosting'or isinstance(kind,list)and'JobPosting'in kind:
                locations=item.get('jobLocation')or[]
                if not isinstance(locations,list):locations=[locations]
                for location in locations[:32]:
                    if isinstance(location,dict):
                        address=location.get('address')or{}
                        if isinstance(address,dict):
                            country=canonical(address.get('addressCountry'))
                            if country:countries.add(country)
    for payload in re.findall(r'''(?is)<script\b[^>]*type=["']application/ld\+json["'][^>]*>(.*?)</script>''',str(html or '')):
        try:inspect(json.loads(payload))
        except (ValueError,TypeError):pass
    # A schema.org JobPosting JSON API is also acceptable, not an arbitrary
    # country mentioned in page/application/company JSON.
    if str(html or '').lstrip().startswith(('{','[')):
        try:inspect(json.loads(html))
        except (ValueError,TypeError):pass
    return next(iter(countries))if len(countries)==1 else ''


class SessionCountryResolver:
    def __init__(self,context,scanner,scope):
        self.context,self.scanner,self.scope=context,scanner,scope
        self.cache={};self.counts=Counter();self.samples=[]

    def resolve(self,url,location,allow_request=True):
        country=country_from_location(location)
        if country:return country,'job_location','observed location'
        # Only genuine geographic ambiguity needs another job response. A
        # missing location is not permission to guess or scrape every detail.
        if len(place_country_candidates(str(location or '')))<2:
            return '','none','unresolved observed location'
        if not allow_request:
            return '','none','structured country pending'
        if url in self.cache:
            self.counts['reused']+=1
            return self.cache[url]
        state=self.scanner._detail_state;state.register(url,self.scope)
        if self.counts['blocked']>=2 or not state.reserve(url,self.scanner.config,browser=False):
            self.counts['budget_skipped']+=1
            return '','none','bounded country-detail pass skipped'
        self.counts['requests']+=1
        response=None
        try:
            time.sleep(.12)  # retained politeness, never a burst of detail reads
            response=self.context.request.get(url,timeout=min(12000,self.scanner.config.DETAIL_SCAN_TIMEOUT_MS))
            text=response.text()[:2000000]
            status=int(response.status)
            reason=challenge_reason(text,status=status,subject='detail')
            metadata=response_metadata(text,status=status,transport='HTTP (assisted session)')
            if reason:
                self.counts['blocked']+=1;state.failure(url,blocked=True,reason=reason,**metadata)
                self.samples.append(dict(url=url,status=status,title=metadata['title'],transport=metadata['transport'],reason=reason))
                result=('','none','country detail access blocked')
            elif status>=400:
                self.counts['failed']+=1;state.failure(url,reason=f'HTTP {status} country detail failed',**metadata)
                result=('','none',f'HTTP {status} country detail unavailable')
            else:
                country=structured_job_country(text,getattr(self.scanner,'_ISO3_TO_COUNTRY',{}))
                state.observe(url,kind='country_observed'if country else'country_unresolved',**metadata)
                if country:
                    self.counts['resolved']+=1
                    result=(country,'structured_job_country','JobPosting.jobLocation.addressCountry')
                else:result=('','none','no unique structured job country')
            self.cache[url]=result
            return result
        except Exception as exc:
            self.counts['failed']+=1
            state.failure(url,reason=type(exc).__name__+': country detail unavailable')
            result=('','none','country detail unavailable');self.cache[url]=result;return result
        finally:
            if response is not None:
                try:response.dispose()
                except Exception:pass

    def report(self):
        return dict(self.counts),self.samples[-4:]
