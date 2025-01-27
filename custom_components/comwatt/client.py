"""Client for Comwatt and Comwatt Legacy."""
from comwatt_client import ComwattClient
from comwatt_client_legacy import ComwattClient as ComwattClientLegacy

class Client:
    def __init__(self, api = "energy"):
        self.api = api
        if self.api == "energy":
            self.client = ComwattClient()
        else:
            self.client = ComwattClientLegacy()

comwatt_client = Client()
