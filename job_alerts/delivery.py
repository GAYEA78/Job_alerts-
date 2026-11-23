"""Escaped, ranked digests sent separately to each recipient."""
import html
import math
import os
import re
import smtplib
import ssl
from email.message import EmailMessage
from datetime import datetime, timezone
from .matching import canonical_url
from .profiles import PROFILES


def address_list(value):
    result=[]
    for address in re.split(r'[,;\n]',value or ''):
        address=address.strip()
        if not address:
            continue
        if not re.fullmatch(r'[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+',address):
            raise ValueError('Invalid email configuration')
        if address.lower() not in {r.lower() for r in result}:
            result.append(address)
    return result


def configuration(profile):
    sender=os.environ.get('GMAIL_ADDRESS','').strip()
    variable='TECH_EMAIL_RECIPIENTS' if profile=='tech' else 'FINANCE_EMAIL_RECIPIENTS'
    recipients=address_list(os.environ.get(variable,'').strip())
    password=os.environ.get('GMAIL_APP_PASSWORD','')
    if not sender or not recipients or not password:
        raise ValueError('Configure GMAIL_ADDRESS, '+variable+' and GMAIL_APP_PASSWORD before sending')
    address_list(sender)
    return sender,recipients,password


def salary_label(job):
    s=job.get('salary') or {}
    adzuna=s.get('source')=='Adzuna' or job.get('source')=='Adzuna'
    if s.get('is_estimate') is True:
        return 'Salary not confirmed by employer (Adzuna estimate hidden)' if adzuna else 'Salary not confirmed by employer (estimate hidden)'
    if adzuna and s.get('is_estimate') is not False:
        return 'Salary not confirmed by employer'
    if s.get('min') or s.get('max'):
        def amount(value):
            try:
                n=float(value)
                return format(n,',.0f') if math.isfinite(n) and n>0 else None
            except (ValueError,TypeError):
                return None
        low,high=amount(s.get('min')),amount(s.get('max'))
        if not low and not high:
            return 'Salary not listed'
        pay=low if low==high else low+'–'+high if low and high else 'from '+low if low else 'up to '+high
        return str(s.get('currency') or '')+' '+pay+' / '+str(s.get('period') or 'unspecified period')
    return str(job.get('salary_text') or 'Salary not listed')


def group_listings(jobs):
    """Group presentation only: every location, link and delivery ID survives."""
    groups={}
    for job in jobs:
        if not canonical_url(job.get('url','')):
            continue
        key=tuple(re.sub(r'\s+',' ',str(job.get(field,'')).strip()).casefold() for field in ('company','title'))
        groups.setdefault(key,[]).append(job)
    return list(groups.values())


def build_email(profile,jobs,extra=0,degraded=False):
    label=PROFILES[profile]['label']
    subject=str(len(jobs))+' '+label+' job matches'
    lines=[subject,'']
    sections=[]
    e=lambda text:html.escape(str(text),quote=True)
    for group in group_listings(jobs):
        heading=group[0]['company']+' — '+group[0]['title']
        lines.append(heading)
        parts=[]
        if len(group)>1:
            group_note=str(len(group))+' related listings; these may be separate openings. Each application link is retained.'
            lines.append(group_note)
            parts.append('<strong>'+e(heading)+'</strong><p>'+e(group_note)+'</p>')
        for j in group:
            url=canonical_url(j['url'])
            location=', '.join(j['locations'])
            posted=datetime.fromtimestamp(j['posted'],timezone.utc).strftime('%b %d, %Y') if j.get('posted') else 'Posting date not listed'
            details=' | '.join([location,salary_label(j),posted])
            reasons='; '.join(j.get('reasons',[]))
            sponsorship=str(j.get('sponsorship') or 'Not stated')
            sources=', '.join(j.get('sources') or [j.get('source','')])
            score=min(j['score'],60) if not j.get('description_complete') else j['score']
            confidence=j.get('confidence','review') if j.get('description_complete') else 'review'
            lines.extend([details,'Match score: '+str(score)+'/100 (rule-based ranking, not a hiring probability)',reasons,'Sponsorship: '+sponsorship,'Source: '+sources,url,''])
            link_label=heading if len(group)==1 else location+' — View listing'
            parts.append('<div style="margin-bottom:16px"><a href="'+e(url)+'"><strong>'+e(link_label)+'</strong></a><br>'+e(details)+'<br><strong>'+e(score)+'/100 match</strong> · '+e(confidence)+'<br>'+e(reasons)+'<br>Sponsorship: '+e(sponsorship)+'<br><small>via '+e(sources)+'</small></div>')
        sections.append('<li style="margin-bottom:22px">'+''.join(parts)+'</li>')
    note=(str(extra)+' additional matches are queued for later digests.' if extra else '')
    if degraded:
        note+=' Some sources were unavailable; this digest has partial coverage.'
    lines.append(note)
    body='<html><body style="font-family:Arial,sans-serif;max-width:780px"><h2>'+html.escape(subject)+'</h2><p>Ranked by role, experience evidence, skills and location. Scores are not hiring probabilities.</p><ol>'+''.join(sections)+'</ol><p>'+html.escape(note)+'</p><p>Verify the employer’s current requirements before applying.</p></body></html>'
    return subject,body,'\n'.join(lines)


def send_email(sender,recipient,password,subject,body,text):
    msg=EmailMessage()
    msg['Subject']=subject
    msg['From']=sender
    msg['To']=recipient
    msg.set_content(text)
    msg.add_alternative(body,subtype='html')
    with smtplib.SMTP_SSL('smtp.gmail.com',465,context=ssl.create_default_context(),timeout=30) as smtp:
        smtp.login(sender,password)
        refused=smtp.send_message(msg,to_addrs=[recipient])
        if refused:
            raise RuntimeError('Mail server refused the recipient')
