from app import config, proxy_store


def test_proxy_password_is_masked_and_server_validated(monkeypatch, tmp_path):
    monkeypatch.setattr(config.settings, 'database_path', str(tmp_path / 'proxy.db'))
    row = proxy_store.create_proxy(owner_id=3, label='mine', server='http://1.2.3.4:8080', username='u', password='hunter2', source='custom')
    assert row['password'] != 'hunter2'
    assert 'hunter2' not in str(row) and row['password']
    assert [p['id'] for p in proxy_store.list_proxies(owner_id=3)] == [row['id']]
    assert proxy_store.list_proxies(owner_id=4) == []
    try:
        proxy_store.create_proxy(owner_id=3, label='bad', server='not a proxy', username=None, password=None, source='custom')
        raise AssertionError('invalid server accepted')
    except ValueError:
        pass


def test_scrape_parser_validates_format_and_caps_at_25():
    raw = '\n'.join([f'10.0.0.{i}:8080' for i in range(40)] + ['bad line', '999.1.1.1:80', '1.2.3.4:notaport'])
    picked = proxy_store.parse_scraped(raw, scheme='http')
    assert len(picked) == 25
    assert all(p['server'].startswith('http://') for p in picked)
    assert all(not p['server'].endswith(':notaport') for p in picked)
