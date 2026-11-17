"""Real SQLite handles are owned across initialization and cleanup failures."""
import sqlite3
from contextlib import contextmanager

import pytest

from src.core import resource_budget
from src.data import database as module
from src.data.database import Database
from src.data.models import ItemCreateDTO
from src.data.repository import ItemRepository


def assert_closed(connection):
    with pytest.raises(sqlite3.ProgrammingError, match='closed'):
        connection.execute('SELECT 1')


@pytest.mark.parametrize('error_type', [RuntimeError, KeyboardInterrupt])
def test_migration_failure_closes_real_handle_and_preserves_primary(tmp_path, monkeypatch, error_type):
    owners = []
    original = error_type('synthetic migration failure')
    connections = []
    def fail(db):
        owners.append(db)
        connections.extend(db._connections)
        raise original
    monkeypatch.setattr(Database, '_run_migrations', fail)
    with pytest.raises(error_type) as caught:
        Database(str(tmp_path/'failed.db'))
    assert caught.value is original and len(connections) == 1
    assert_closed(connections[0])
    owner = owners[0]
    assert owner._closed and owner._connections == owner._failed_connections == []
    assert any(str(tmp_path/'failed.db') == str(path)
               for path in resource_budget._owned_roots[owner])


@pytest.mark.parametrize('statement', ['PRAGMA journal_mode = DELETE', 'PRAGMA foreign_keys = ON',
                                     'PRAGMA busy_timeout = 5000'])
@pytest.mark.parametrize('error_type', [sqlite3.OperationalError, KeyboardInterrupt])
def test_configuration_failure_closes_unpublished_real_connection(tmp_path, monkeypatch, statement, error_type):
    opened, owners = [], []
    primary = error_type('synthetic configuration failure')
    class Connection(sqlite3.Connection):
        def execute(self, sql, *args, **kwargs):
            if sql == statement:
                raise primary
            return super().execute(sql, *args, **kwargs)
    real_connect = sqlite3.connect
    def connect(*args, **kwargs):
        connection = real_connect(*args, factory=Connection, **kwargs)
        opened.append(connection)
        return connection
    original_initialize = Database._initialize
    def initialize(db):
        owners.append(db)
        original_initialize(db)
    monkeypatch.setattr(module.sqlite3, 'connect', connect)
    monkeypatch.setattr(Database, '_initialize', initialize)
    with pytest.raises(error_type) as caught:
        Database(str(tmp_path/'failed.db'))
    assert caught.value is primary and len(opened) == 1
    assert_closed(opened[0])
    assert not hasattr(owners[0]._local, 'connection')
    assert owners[0]._connections == owners[0]._failed_connections == []


def test_cleanup_failure_keeps_unpublished_handle_owned_and_original_error(tmp_path, monkeypatch):
    opened, owners = [], []
    primary = sqlite3.OperationalError('synthetic configuration failure')
    cleanup = RuntimeError('synthetic cleanup failure')
    class Connection(sqlite3.Connection):
        fail_close = True
        close_calls = 0
        def execute(self, sql, *args, **kwargs):
            if sql == 'PRAGMA foreign_keys = ON':
                raise primary
            return super().execute(sql, *args, **kwargs)
        def close(self):
            self.close_calls += 1
            if self.fail_close:
                raise cleanup
            return super().close()
    real_connect = sqlite3.connect
    def connect(*args, **kwargs):
        conn = real_connect(*args, factory=Connection, **kwargs)
        opened.append(conn)
        return conn
    original_initialize = Database._initialize
    def initialize(db):
        owners.append(db)
        original_initialize(db)
    monkeypatch.setattr(module.sqlite3, 'connect', connect)
    monkeypatch.setattr(Database, '_initialize', initialize)
    with pytest.raises(sqlite3.OperationalError) as caught:
        Database(str(tmp_path/'failed.db'))
    owner, connection = owners[0], opened[0]
    try:
        assert caught.value is primary and connection.close_calls == 2
        assert owner._connections == [] and owner._failed_connections == [connection]
        assert not hasattr(owner._local, 'connection')
        assert connection.execute('SELECT 1').fetchone()[0] == 1
        connection.fail_close = False
        owner.close()
        assert owner._failed_connections == []
        assert_closed(connection)
    finally:
        sqlite3.Connection.close(connection)


@pytest.mark.parametrize('error_type', [RuntimeError, KeyboardInterrupt])
def test_close_attempts_every_handle_and_retries_only_failed_ones(tmp_path, error_type):
    db = Database(str(tmp_path/'normal.db'))
    normal = db.get_connection()
    primary = error_type('first close failure')
    class Connection(sqlite3.Connection):
        fail = True
        calls = 0
        def close(self):
            self.calls += 1
            if self.fail:
                raise primary
            return super().close()
    first = sqlite3.connect(':memory:', factory=Connection)
    last = sqlite3.connect(':memory:')
    db._connections[:] = [first, normal, last]
    try:
        with pytest.raises(error_type) as caught:
            db.close()
        assert caught.value is primary and first.calls == 1
        assert_closed(normal); assert_closed(last)
        assert db._connections == [] and db._failed_connections == [first]
        with pytest.raises(RuntimeError, match='Database is closed'):
            db.get_connection()
        first.fail = False
        db.close()
        assert first.calls == 2 and db._failed_connections == []
        assert_closed(first)
    finally:
        sqlite3.Connection.close(first)
        db.close()


def test_normal_crud_and_readonly_open_keep_existing_data(tmp_path, monkeypatch):
    path = tmp_path/'normal.db'
    db = Database(str(path))
    repo = ItemRepository(db)
    identifier = repo.insert(ItemCreateDTO(item_type='text', source_mode='import', text_content='kept'))
    assert repo.get_by_id(identifier).text_content == 'kept'
    db.close()
    @contextmanager
    def reject(**kwargs):
        raise resource_budget.ResourceBudgetExceeded('disk')
        yield
    monkeypatch.setattr(module, 'disk_quota_guard', reject)
    readonly = Database(str(path))
    try:
        assert readonly.read_only and ItemRepository(readonly).get_by_id(identifier).text_content == 'kept'
        with pytest.raises(sqlite3.OperationalError, match='readonly'):
            readonly.get_connection().execute("DELETE FROM items")
        assert readonly._failed_connections == []
    finally:
        readonly.close()


def test_readonly_configuration_failure_closes_only_new_unpublished_handle(tmp_path, monkeypatch):
    db = Database(str(tmp_path/'normal.db'))
    existing = db.get_connection()
    del db._local.connection
    db.read_only = True
    opened = []
    primary = sqlite3.OperationalError('synthetic readonly setup failure')
    class Connection(sqlite3.Connection):
        def execute(self, sql, *args, **kwargs):
            if sql == 'PRAGMA query_only = ON':
                raise primary
            return super().execute(sql, *args, **kwargs)
    real_connect = sqlite3.connect
    def connect(*args, **kwargs):
        connection = real_connect(*args, factory=Connection, **kwargs)
        opened.append(connection)
        return connection
    monkeypatch.setattr(module.sqlite3, 'connect', connect)
    try:
        with pytest.raises(sqlite3.OperationalError) as caught:
            db.get_connection()
        assert caught.value is primary and len(opened) == 1
        assert_closed(opened[0])
        assert not hasattr(db._local, 'connection')
        assert db._connections == [existing] and db._failed_connections == []
        assert existing.execute('SELECT 1').fetchone()[0] == 1
    finally:
        db.close()


def test_row_factory_configuration_failure_also_closes_unpublished_handle(tmp_path, monkeypatch):
    opened = []
    primary = RuntimeError('synthetic row factory failure')
    class Connection(sqlite3.Connection):
        def __setattr__(self, name, value):
            if name == 'row_factory':
                raise primary
            return super().__setattr__(name, value)
    real_connect = sqlite3.connect
    def connect(*args, **kwargs):
        connection = real_connect(*args, factory=Connection, **kwargs)
        opened.append(connection)
        return connection
    monkeypatch.setattr(module.sqlite3, 'connect', connect)
    with pytest.raises(RuntimeError) as caught:
        Database(str(tmp_path/'failed.db'))
    assert caught.value is primary and len(opened) == 1
    assert_closed(opened[0])
