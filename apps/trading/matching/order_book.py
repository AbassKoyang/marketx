from collections import defaultdict, deque
from dataclasses import dataclass
import heapq


@dataclass(frozen=True)
class BookOrder:
    id: int
    side: str
    price: int
    remaining_quantity: int
    sequence: int


class OrderBook:
    """In-memory price-time book used for deterministic tests and snapshots."""

    def __init__(self):
        self._buy_heap = []
        self._sell_heap = []
        self._buys = defaultdict(deque)
        self._sells = defaultdict(deque)
        self._cancelled = set()

    def add(self, order):
        levels = self._buys if order.side == "buy" else self._sells
        heap = self._buy_heap if order.side == "buy" else self._sell_heap
        heap_price = -order.price if order.side == "buy" else order.price

        levels[order.price].append(order)
        heapq.heappush(heap, heap_price)

    def cancel(self, order_id):
        self._cancelled.add(order_id)

    def best_bid(self):
        return self._best("buy")

    def best_ask(self):
        return self._best("sell")

    def depth(self, side, limit=20):
        levels = self._buys if side == "buy" else self._sells
        prices = sorted(levels.keys(), reverse=side == "buy")
        rows = []
        for price in prices:
            quantity = sum(
                order.remaining_quantity
                for order in levels[price]
                if order.id not in self._cancelled and order.remaining_quantity > 0
            )
            if quantity:
                rows.append({"price": price, "quantity": quantity})
            if len(rows) >= limit:
                break
        return rows

    def _best(self, side):
        heap = self._buy_heap if side == "buy" else self._sell_heap
        levels = self._buys if side == "buy" else self._sells

        while heap:
            heap_price = heap[0]
            price = -heap_price if side == "buy" else heap_price
            queue = levels.get(price)
            while queue and (
                queue[0].id in self._cancelled or queue[0].remaining_quantity <= 0
            ):
                queue.popleft()
            if queue:
                return queue[0]
            heapq.heappop(heap)
            levels.pop(price, None)
        return None
