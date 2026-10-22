import json
import sqlite3

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework.test import APIClient

from .data_preparation import prepare_data
from .models import Entity, Membership, Project


def config(**overrides):
    return {
        'fields': [
            {'name': 'name', 'type': 'string', 'value': "测试 O'Brien\\sample"},
            {'name': 'age', 'type': 'integer', 'value': '18'},
            {'name': 'price', 'type': 'number', 'value': '9.5'},
            {'name': 'enabled', 'type': 'boolean', 'value': 'false'},
            {'name': 'extra', 'type': 'json', 'value': '{"tags":["new"],"count":2}'},
            {'name': 'deleted_at', 'type': 'null', 'value': ''},
        ],
        'output_format': 'json', 'table_name': 'test_users', 'dialect': 'sqlite',
        **overrides,
    }


class GenerationTests(SimpleTestCase):
    def test_json_types_and_array_shape(self):
        result = prepare_data(config(json_shape='array'))
        values = json.loads(result['generated_json'])
        self.assertEqual(values, [result['variables']])
        self.assertEqual(values[0]['age'], 18)
        self.assertIs(values[0]['enabled'], False)
        self.assertIsNone(values[0]['deleted_at'])
        self.assertEqual(values[0]['extra']['tags'], ['new'])
        self.assertEqual(result['generated_sql'], '')

    def test_sqlite_insert_roundtrip_and_quote_injection(self):
        options = config(output_format='sql')
        value = "'); DROP TABLE test_users; --\\中文\n"
        options['fields'][0]['value'] = value
        result = prepare_data(options)
        with sqlite3.connect(':memory:') as connection:
            connection.execute('CREATE TABLE test_users (name TEXT,age INTEGER,price REAL,enabled INTEGER,extra TEXT,deleted_at TEXT)')
            connection.executescript(result['generated_sql'])
            row = connection.execute('SELECT * FROM test_users').fetchone()
        self.assertEqual(row[0], value)
        self.assertEqual(row[1:4], (18, 9.5, 0))
        self.assertEqual(json.loads(row[4]), result['variables']['extra'])
        self.assertIsNone(row[5])

    def test_dialects_escape_without_executing(self):
        postgres = prepare_data(config(output_format='sql', dialect='postgresql'))
        self.assertIn("E'测试 O''Brien\\\\sample'", postgres['generated_sql'])
        mysql = prepare_data(config(output_format='sql', dialect='mysql'))
        encoded = "测试 O'Brien\\sample".encode().hex()
        self.assertIn(f"CONVERT(X'{encoded}' USING utf8mb4)", mysql['generated_sql'])
        self.assertIn('INSERT INTO `test_users`', mysql['generated_sql'])

    def test_invalid_names_duplicates_types_and_nonfinite_values(self):
        invalid = [
            config(fields=[]), config(table_name='users; DROP TABLE users', output_format='sql'),
            config(fields=[{'name': 'name', 'type': [], 'value': ''}]),
            config(fields=[{'name': 'x', 'type': 'boolean', 'value': '1'}]),
            config(fields=[{'name': 'x', 'type': 'number', 'value': 'NaN'}]),
            config(fields=[{'name': 'x', 'type': 'integer', 'value': '9007199254740992'}]),
            config(fields=[{'name': 'x', 'type': 'json', 'value': '{"n":1e999}'}]),
            config(fields=[{'name': 'x', 'type': 'json', 'value': '[NaN]'}]),
            config(fields=[{'name': 'x', 'type': 'string', 'value': 'a'}, {'name': ' x ', 'type': 'string', 'value': 'b'}]),
        ]
        for options in invalid:
            with self.subTest(options=options), self.assertRaises(ValueError):prepare_data(options)

    def test_plaintext_secrets_rejected_and_references_remain_unresolved(self):
        options = config(fields=[{'name': 'api_key', 'type': 'string', 'value': 'not-a-real-key'}])
        with self.assertRaisesRegex(ValueError, '敏感字段'):prepare_data(options)
        options['fields'][0]['value'] = '{{secret:DEMO}}'
        self.assertEqual(prepare_data(options)['variables']['api_key'], '{{secret:DEMO}}')
        with self.assertRaisesRegex(ValueError, '敏感字段'):
            prepare_data(config(fields=[{'name': 'payload', 'type': 'json', 'value': '{"password":"not-real"}'}]))

    def test_null_discards_unused_values_and_sql_case_collisions_are_rejected(self):
        result=prepare_data(config(fields=[{'name':'api_key','type':'null','value':'unused-sensitive-text'}]))
        self.assertEqual(result['fields'][0]['value'],'')
        self.assertIsNone(result['variables']['api_key'])
        fields=[{'name':name,'type':'string','value':'test'} for name in ['name','NAME']]
        for dialect in ['sqlite','mysql']:
            with self.subTest(dialect=dialect),self.assertRaises(ValueError):
                prepare_data(config(fields=fields,output_format='sql',dialect=dialect))
        self.assertIn('"NAME"',prepare_data(config(fields=fields,output_format='sql',dialect='postgresql'))['generated_sql'])

    def test_random_batch_ranges_types_and_reproducible_export(self):
        options = config(count=20, fields=[
            {'name': 'name', 'type': 'string', 'random': True, 'length': 8},
            {'name': 'age', 'type': 'integer', 'random': True, 'min': 18, 'max': 60},
            {'name': 'ratio', 'type': 'number', 'random': True, 'min': 0.001, 'max': 0.002},
            {'name': 'enabled', 'type': 'boolean', 'random': True},
            {'name': 'fixed', 'type': 'string', 'value': '固定值'},
        ])
        result = prepare_data(options)
        rows = json.loads(result['generated_json'])
        self.assertEqual(len(rows), 20)
        self.assertEqual(result['json_shape'], 'array')
        self.assertEqual(result['variables'], rows[0])
        for row in rows:
            self.assertRegex(row['name'], r'^[A-Za-z0-9]{8}$')
            self.assertTrue(18 <= row['age'] <= 60)
            self.assertIs(type(row['age']), int)
            self.assertTrue(0.001 <= row['ratio'] <= 0.002)
            self.assertIs(type(row['enabled']), bool)
            self.assertEqual(row['fixed'], '固定值')
        self.assertGreater(len({row['name'] for row in rows}), 1)
        self.assertEqual(prepare_data(result), result)
        sql = prepare_data({**result, 'output_format': 'sql'})
        with sqlite3.connect(':memory:') as connection:
            connection.execute('CREATE TABLE test_users (name TEXT,age INTEGER,ratio REAL,enabled INTEGER,fixed TEXT)')
            connection.executescript(sql['generated_sql'])
            values = connection.execute('SELECT name,age FROM test_users').fetchall()
        self.assertEqual(values, [(row['name'], row['age']) for row in rows])

    def test_invalid_batch_and_random_parameters(self):
        invalid = [config(count=value) for value in [0, 1001, True, 1.5, '2', None]]
        for field in [
            {'name':'x','type':'string','random':True,'length':0},
            {'name':'x','type':'integer','random':True,'min':3,'max':2},
            {'name':'x','type':'integer','random':True,'min':1.5},
            {'name':'x','type':'number','random':True,'min':float('nan')},
            {'name':'x','type':'json','random':True},
            {'name':'api_key','type':'string','random':True},
            {'name':'x','type':'string','random':'true'},
        ]:
            invalid.append(config(fields=[field]))
        invalid.append(config(count=1000, fields=[{'name':'x','type':'string','value':'a'*20000}]))
        for options in invalid:
            with self.subTest(options=options), self.assertRaises(ValueError):
                prepare_data(options)
        self.assertEqual(len(json.loads(prepare_data(config(count=1000,fields=[{'name':'n','type':'integer','value':'1'}]))['generated_json'])),1000)

    def test_example_formats_and_types_roundtrip(self):
        examples = [
            ('phone','string','13800138000'), ('email','string','tester01@example.com'),
            ('order_no','string','ORD-0001'), ('full_name','string','张三'),
            ('date','string','2024-02-29'), ('timestamp','string','2024/02/29 13:05:09'),
            ('uuid','string','123e4567-e89b-12d3-a456-426614174000'),
            ('age','integer','-18'), ('price','number','12.50'),
            ('enabled','boolean','true'), ('code','string','00120'),
        ]
        fields = [{'name':name,'type':kind,'value':value,'random':True,'random_mode':'example'}
                  for name,kind,value in examples]
        result = prepare_data(config(count=10, fields=fields))
        rows = json.loads(result['generated_json'])
        from datetime import datetime
        from uuid import UUID
        for row in rows:
            self.assertRegex(row['phone'], r'^138[0-9]{8}$')
            self.assertRegex(row['email'], r'^tester[0-9]{2}@example\.com$')
            self.assertRegex(row['order_no'], r'^ORD-[0-9]{4}$')
            self.assertRegex(row['full_name'], r'^[\u4e00-\u9fff]{2}$')
            datetime.strptime(row['date'], '%Y-%m-%d')
            datetime.strptime(row['timestamp'], '%Y/%m/%d %H:%M:%S')
            self.assertEqual(UUID(row['uuid']).version, 4)
            self.assertTrue(-99 <= row['age'] <= -10)
            self.assertIs(type(row['age']), int)
            self.assertTrue(10 <= row['price'] < 100)
            self.assertIs(type(row['enabled']), bool)
            self.assertRegex(row['code'], r'^[0-9]{5}$')
        self.assertGreater(len({row['phone'] for row in rows}), 1)
        self.assertEqual(result['fields'][0]['value'], '13800138000')
        self.assertEqual(prepare_data(result), result)
        sql = prepare_data({**result,'output_format':'sql'})
        self.assertIn(rows[0]['phone'], sql['generated_sql'])
        self.assertEqual(sql['generated_sql'].count('INSERT INTO'), 10)

    def test_example_validation_and_fixed_fields(self):
        implicit_type = prepare_data(config(fields=[{'name':'label','value':'ORD-0001','random':True,'random_mode':'example'}]))
        self.assertRegex(implicit_type['variables']['label'], r'^ORD-[0-9]{4}$')
        for example, kind in [('', 'string'), ('x'*257,'string'), ('abc','integer'),
                              ('1e9','number'), ('2024-02-30','string'),
                              ('{{secret:DEMO}}','string')]:
            with self.subTest(example=example), self.assertRaises(ValueError):
                prepare_data(config(fields=[{'name':'sample','type':kind,'value':example,'random':True,'random_mode':'example'}]))
        fixed = prepare_data(config(fields=[{'name':'label','type':'string','value':'ORD-0001','random':False,'random_mode':'example'}],count=2))
        self.assertEqual(json.loads(fixed['generated_json']),[{'label':'ORD-0001'}]*2)


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class PreparationAPITests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user('owner')
        self.reader = get_user_model().objects.create_user('reader')
        self.outsider = get_user_model().objects.create_user('outsider')
        self.project = Project.objects.create(name='测试项目', key='DATA', owner=self.owner)
        Membership.objects.create(project=self.project, user=self.reader, role='viewer')
        self.client = APIClient()
        self.client.force_authenticate(self.owner)

    def create(self, **overrides):
        response = self.client.post('/api/entities/', {
            'project': self.project.pk, 'kind': 'data_preparation', 'name': '测试数据',
            'config': config(), **overrides,
        }, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        return response.data

    def test_preview_does_not_save_and_save_recomputes_outputs(self):
        response = self.client.post('/api/entities/prepare_data/', {'project': self.project.pk, 'config': config()}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(Entity.objects.count(), 0)
        data = self.create(config=config(generated_json='forged', variables={'injected': 'value'}))
        self.assertEqual(data['config'], response.data)
        self.assertNotIn('injected', data['config']['variables'])
        loaded = self.client.get(f'/api/entities/{data["id"]}/')
        self.assertEqual(loaded.data['config'], response.data)
        updated = self.client.patch(f'/api/entities/{data["id"]}/', {'version': 1, 'name': '新名称'}, format='json')
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.data['version'], 2)
        self.assertEqual(Entity.objects.get(pk=data['id']).revisions.count(), 2)
        self.assertEqual(self.client.patch(f'/api/entities/{data["id"]}/', {'version': 1, 'name': '覆盖'}, format='json').status_code, 400)

    def test_downloads_filtering_pagination_and_delete(self):
        data = self.create(config=config(output_format='sql'))
        response = self.client.get(f'/api/entities/{data["id"]}/prepared_output/')
        self.assertEqual(response.status_code, 200)
        self.assertIn('application/sql', response['Content-Type'])
        self.assertEqual(response.content.decode(), data['config']['generated_sql'])
        response = self.client.get(f'/api/entities/{data["id"]}/prepared_output/?output_format=json')
        self.assertEqual(json.loads(response.content), data['config']['variables'])
        for index in range(11):self.create(name=f'JSON {index}')
        query = f'/api/entities/?project={self.project.pk}&kind=data_preparation&output_format=json'
        response = self.client.get(query)
        self.assertEqual(response.data['count'], 11)
        self.assertEqual(len(response.data['results']), 10)
        self.assertEqual(len(self.client.get(query+'&page=2').data['results']), 1)
        self.assertEqual(self.client.get(query+'&search=JSON+3').data['count'], 1)
        self.assertEqual(self.client.delete(f'/api/entities/{data["id"]}/').status_code, 204)

    def test_project_isolation_reader_and_anonymous_permissions(self):
        data = self.create()
        self.client.force_authenticate(self.reader)
        self.assertEqual(self.client.get(f'/api/entities/{data["id"]}/prepared_output/').status_code, 200)
        response = self.client.post('/api/entities/prepare_data/', {'project': self.project.pk, 'config': config()}, format='json')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.client.patch(f'/api/entities/{data["id"]}/', {'name': '修改'}, format='json').status_code, 403)
        self.assertEqual(self.client.delete(f'/api/entities/{data["id"]}/').status_code, 403)
        self.client.force_authenticate(self.outsider)
        self.assertEqual(self.client.get(f'/api/entities/{data["id"]}/prepared_output/').status_code, 404)
        self.assertEqual(self.client.get(f'/api/entities/?project={self.project.pk}&kind=data_preparation').data['count'], 0)
        self.client.force_authenticate(None)
        self.assertIn(self.client.get('/api/entities/').status_code, [401, 403])

    def test_invalid_config_is_400_and_never_persisted(self):
        response = self.client.post('/api/entities/', {'project': self.project.pk, 'kind': 'data_preparation', 'name': '错误', 'config': config(fields=[])}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Entity.objects.count(), 0)

    def test_random_batch_persists_and_download_does_not_regenerate(self):
        data = self.create(config=config(count=5, fields=[{'name':'label','type':'string','random':True,'length':12}]))
        expected = data['config']['generated_json']
        endpoint = f'/api/entities/{data["id"]}/prepared_output/'
        self.assertEqual(self.client.get(endpoint).content.decode(), expected)
        self.assertEqual(self.client.get(endpoint).content.decode(), expected)
        updated = self.client.patch(f'/api/entities/{data["id"]}/', {'version':1,'name':'改名'}, format='json')
        self.assertEqual(updated.data['config']['generated_json'], expected)
