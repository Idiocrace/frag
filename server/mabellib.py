import threading


class Cache:
    def __init__(self):
        self.items = {}

    def add(self, key, value, lifetime):
        self.items[key] = CacheItem(self, key, value, lifetime)

    def get(self, key):
        item = self.items.get(key)

        if item is None:
            return None

        return item.value

    def get_item(self, key):
        return self.items.get(key)

    def contains(self, key):
        return key in self.items

    def remove(self, key):
        if key in self.items:
            del self.items[key]


class CacheItem:
    def __init__(self, cache, key, value, lifetime):
        self.cache = cache
        self.key = key
        self.value = value

        self.timer = threading.Timer(lifetime, self.expire)
        self.timer.start()

    def expire(self):
        self.cache.remove(self.key)
