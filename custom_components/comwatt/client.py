"""Client for Comwatt and Comwatt Legacy."""
from comwatt_client import ComwattClient
from comwatt_client_legacy import ComwattClient as ComwattClientLegacy

comwatt_client = ComwattClient()
comwatt_client_legacy = ComwattClientLegacy()

 def get_client(legacy: bool = False):                                                                                                                                                                                                 
     """Return the appropriate client based on legacy flag."""                                                                                                                                                                         
     if legacy:                                                                                                                                                                                                                        
         return ComwattClientLegacy()                                                                                                                                                                                                  
     return ComwattClient()                                                                                                                                                                                                            
