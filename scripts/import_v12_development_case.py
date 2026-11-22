"""Migrate a verified SCRIPTED development incident, never a research collection case."""
import argparse
from pathlib import Path
from assembly_v12.contracts import read
from assembly_v12.memory import Library


def migrate(run,old_memory,new_memory):
    run=Path(run);result=read(run/'result.json')
    if result['policy_source']!='scripted' or result['split']!='development' or result['status']!='success':
        raise ValueError('Only a physically successful, declared scripted development fixture is accepted.')
    old={read(p)['experience']['id']:read(p) for p in Path(old_memory).glob('*/archive.json')
         if read(p)['experience']['run_id']==result['run_id']}
    library=Library(new_memory,allowed_splits=('development',))
    imported=[]
    for incident in read(run/'incidents.json'):
        source=old[incident['id']]
        if source['guidance_source']!='scripted_development_fixture':raise ValueError('Mismatched guidance provenance.')
        imported.append(library.register(incident,source['guidance'],source['scope'],run,
                                          guidance_source='scripted_development_fixture'))
    return imported


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('run');p.add_argument('old_memory');p.add_argument('new_memory')
    a=p.parse_args();print(__import__('json').dumps(migrate(a.run,a.old_memory,a.new_memory)))
