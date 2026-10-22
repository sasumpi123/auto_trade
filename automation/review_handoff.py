"""Generate a metadata-only review packet; stdout only, no local or remote writes."""
import json
import os

from .handoff_common import (HandoffReader, Parser, differences, halted, load_policy,
                             previous_snapshot, render, safe_text)


def main():
    parser=Parser(description=__doc__)
    parser.add_argument('--pr',type=int,required=True)
    parser.add_argument('--format',choices=('json','markdown','prompt'),default='json')
    parser.add_argument('--previous')
    parser.add_argument('--purpose',default='')
    parser.add_argument('--save-packet',help='Create a new local JSON packet; never overwrite')
    args=parser.parse_args()
    try:
        context=safe_text(args.purpose)
        before=previous_snapshot(args.previous,args.pr) if args.previous else None
        report=HandoffReader(load_policy()).collect(args.pr)
        report['comparison']=differences(before,report['snapshot'])
        if context:report['untrusted_context']=context
        report['duplicate_handoff']=report['comparison']['status']=='UNCHANGED'
        if args.save_packet:
            descriptor=os.open(args.save_packet,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
            with os.fdopen(descriptor,'w',encoding='utf-8') as stream:
                json.dump(report,stream,ensure_ascii=False,indent=2)
    except Exception as error:
        report=halted(error)
    print(render(report,args.format))
    return 2 if report['workflow_state']=='HALTED' else 0


if __name__=='__main__':
    raise SystemExit(main())
