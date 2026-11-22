"""Escaped, ranked digests sent separately to each recipient."""
import html
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
    if s.get('min') or s.get('max'):
        return str(s.get('currency',''))+' '+str(s.get('min') or '?')+'–'+str(s.get('max') or '?')+' / '+str(s.get('period','unspecified period'))
    return str(job.get('salary_text') or 'Salary not listed')


def build_email(profile,jobs,extra=0,degraded=False):
    label=PROFILES[profile]['label']
    subject=str(len(jobs))+' '+label+' job matches'
    lines=[subject,'']
    sections=[]
    for j in jobs:
        url=canonical_url(j.get('url',''))
        if not url:
            continue
        heading=j['company']+' — '+j['title']
        location=', '.join(j['locations'])
        posted=datetime.fromtimestamp(j['posted'],timezone.utc).strftime('%b %d, %Y') if j.get('posted') else 'Posting date not listed'
        details=' | '.join([location,salary_label(j),posted])
        reasons='; '.join(j.get('reasons',[]))
        sponsorship=str(j.get('sponsorship') or 'Not stated')
        sources=', '.join(j.get('sources') or [j.get('source','')])
        lines.extend([heading,details,'Match score: '+str(j['score'])+'/100 (rule-based ranking, not a hiring probability)',reasons,'Sponsorship: '+sponsorship,'Source: '+sources,url,''])
        e=lambda text:html.escape(str(text),quote=True)
        sections.append('<li style="margin-bottom:22px"><a href="'+e(url)+'"><strong>'+e(heading)+'</strong></a><br>'+e(details)+'<br><strong>'+e(j['score'])+'/100 match</strong> · '+e(j.get('confidence','review'))+'<br>'+e(reasons)+'<br>Sponsorship: '+e(sponsorship)+'<br><small>via '+e(sources)+'</small></li>')
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
