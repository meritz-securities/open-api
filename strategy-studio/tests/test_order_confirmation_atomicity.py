from concurrent.futures import ThreadPoolExecutor

from meritz import safety


def test_same_confirmation_token_is_accepted_once_under_concurrency():
    class AtomicOnlyStore:
        def __init__(self):
            self.inner = safety._MemoryStore()

        def put(self, *args):
            self.inner.put(*args)

        def purge(self, *args):
            self.inner.purge(*args)

        def consume(self, *args):
            return self.inner.consume(*args)

        def get(self, *_args):
            raise AssertionError('confirmation checks must use atomic consume')

        def pop(self, *_args):
            raise AssertionError('confirmation checks must not split check and delete')

    previous = safety._store
    store = AtomicOnlyStore()
    safety.set_confirm_store(store)
    try:
        body = {'iscd': 'A005930', 'odqt': '1'}
        token = safety.issue_confirm_token('orders_buy', body, 'https://example.invalid', 'test-app')
        with ThreadPoolExecutor(max_workers=16) as pool:
            results = list(pool.map(lambda _: safety.check_confirm(
                'orders_buy', body, token, 'https://example.invalid', 'test-app'), range(64)))
        assert results.count(None) == 1
        assert all(result is None or '유효하지 않거나 만료' in result for result in results)
    finally:
        safety.set_confirm_store(previous)
