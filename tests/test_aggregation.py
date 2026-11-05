import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from aggregation import project_sources
from provider_yaml import parse_provider, load_provider_archive
from sources import load_sources
from merging import merge_category
from convert import build


def source():
    return {'id':'catalog', 'publish':False,
            'documents':{name:{'version':1,'rules':[{'domain_suffix':[name+'.example']}]} for name in ('one','two','new')},
            'category_map':{name:name for name in ('one','two','new')},
            'labels':{'one':{'scope':'global','provider':'one'}, 'two':{'scope':'cn','provider':'two'}, 'new':{'scope':'global','provider':'new'}},
            'missing_categories':[]}


class AggregateTests(unittest.TestCase):
    def test_hidden_catalog_scope_and_automatic_new_provider(self):
        original=source()
        output=project_sources([original],[
            {'target':'ai-all','select':[{'source':'catalog'}]},
            {'target':'ai-cn','select':[{'source':'catalog','where':{'scope':'cn'}}]},
            {'target':'ai-vendor','select':[{'source':'catalog','where':{'provider':['one','two']}}]}])
        self.assertEqual(set(output[0]['documents']), {'ai-all','ai-cn','ai-vendor'})
        self.assertEqual(output[0]['members']['ai-all'],['new','one','two'])
        self.assertEqual(output[0]['members']['ai-cn'],['two'])
        self.assertEqual(output[0]['members']['ai-vendor'],['one','two'])
        self.assertEqual(set(original['documents']), {'one','two','new'})
        self.assertEqual(set(merge_category('ai-cn',output)[0]['rules'][0]['domain_suffix']), {'two.example'})

    def test_augmentation_preserves_existing_target(self):
        base={'id':'base','documents':{'existing':{'version':1,'rules':[{'domain':['base.example']}]}},'category_map':{'existing':'existing'}}
        projected=project_sources([base,source()],[{'target':'existing','select':[{'source':'catalog','where':{'scope':'global'}}]}])
        merged,provenance=merge_category('existing',projected)
        self.assertIn('base.example',merged['rules'][0]['domain'])
        self.assertNotIn('two.example',merged['rules'][0]['domain_suffix'])

    def test_empty_selector_and_duplicate_aggregation_fail(self):
        empty={'target':'missing','select':[{'source':'catalog','where':{'provider':'unknown'}}]}
        with self.assertRaises(ValueError):project_sources([source()],[empty])
        item={'target':'all','select':[{'source':'catalog'}]}
        with self.assertRaises(ValueError):project_sources([source()],[item,item])

    def test_nested_output_and_no_individual_provider_files(self):
        projected=project_sources([source()],[{'target':'ai-all','select':[{'source':'catalog'}]}])
        with tempfile.TemporaryDirectory() as temp,patch('convert.subprocess.run',return_value=SimpleNamespace(stdout='decoder fixture')):
            manifest=build(projected,Path(temp)/'dist',output_paths={'ai-all':'ai/all'})
            self.assertEqual(manifest['categories']['ai-all']['ruleset'],'ruleset/ai/all.list')
            self.assertEqual(len(list((Path(temp)/'dist/ruleset').rglob('*.list'))),1)
            with self.assertRaises(ValueError):build(projected,Path(temp)/'bad',output_paths={'ai-all':'../bad'})


class ProviderTests(unittest.TestCase):
    def test_full_provider_preserves_regex_process_ip_asn_and_scope(self):
        data='''provider: example
scope: cn
groups:
  - name: Core
    rules:
      domainSuffix: [example.com]
      domainRegex: ['^api[0-9]+\\.example\\.com$']
      processName: ['Example AI']
      ipCidr6: ['2001:db8::/32']
      asn: [1234]
  - name: Third-Party
    rules:
      domain: [shared.example]
'''
        name,document,labels=parse_provider(data)
        self.assertEqual(labels['scope'],'cn')
        source_item={'id':'test','documents':{'test':document},'category_map':{'test':'test'}}
        normalized=merge_category('test',[source_item])[0]
        fields={k for rule in normalized['rules'] for k in rule}
        self.assertEqual(fields, {'domain_suffix','domain','domain_regex','process_name','ip_cidr','ip_asn'})
        filtered=parse_provider(data,['Third-Party'])[1]
        self.assertFalse(any('domain' in r for r in filtered['rules']))

    def test_archive_catalog_discovery_and_notice(self):
        data=io.BytesIO()
        with tarfile.open(fileobj=data,mode='w:gz') as archive:
            for name,content in [('repo/data/providers/test.yaml',b'provider: test\nrules:\n  domainSuffix: [example.com]\n'),('repo/LICENSE',b'License fixture')]:
                info=tarfile.TarInfo(name);info.size=len(content);archive.addfile(info,io.BytesIO(content))
        config={'id':'catalog','format':'provider-yaml-archive','url':'https://example.com/archive','catalog':True,'publish':False,'notice_paths':['LICENSE']}
        loaded=load_sources([config],['unrelated'],fetch=lambda url:data.getvalue())
        self.assertEqual(set(loaded[0]['documents']),{'test'})
        self.assertEqual(loaded[0]['notices']['LICENSE'],'License fixture')
        self.assertFalse(loaded[0]['publish'])
