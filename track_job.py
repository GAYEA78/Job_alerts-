"""Record an application status without contacting any employer."""
import argparse
import os
from job_alerts.storage import State


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('profile',choices=('tech','finance'))
    parser.add_argument('url')
    parser.add_argument('status',choices=('saved','applied','interviewing','rejected','dismissed','reset'))
    args=parser.parse_args()
    State(os.environ.get('JOB_ALERT_HOME','state')).track(args.profile,args.url,args.status)
    print('Application status saved.')


if __name__=='__main__':
    main()
