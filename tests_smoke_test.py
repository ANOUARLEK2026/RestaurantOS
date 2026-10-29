"""RestaurantOS local API smoke test.
Run while START-RESTAURANTOS.bat/server.py is active: python tests_smoke_test.py
"""
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import json

BASE='http://127.0.0.1:4000'

def call(path, method='GET', data=None, token=''):
    body=json.dumps(data).encode() if data is not None else None
    req=Request(BASE+path,data=body,method=method,headers={'Content-Type':'application/json'})
    if token:req.add_header('X-Session-Token',token)
    with urlopen(req,timeout=5) as r:
        return json.loads(r.read().decode())


def main():
    health=call('/api/health')
    assert health['ok']
    login=call('/api/login','POST',{'email':'admin@restaurantos.local','password':'Admin@123'})
    token=login['token']
    menu=call('/api/menu',token=token)
    assert menu['items']
    order=call('/api/orders','POST',{'table_id':1,'items':[{'menu_item_id':menu['items'][0]['id'],'quantity':1}]},token)
    oid=order['order_id']
    call('/api/orders/status','POST',{'order_id':oid,'status':'preparing'},token)
    call('/api/orders/status','POST',{'order_id':oid,'status':'ready'},token)
    call('/api/payments','POST',{'order_id':oid,'payment_method':'cash','amount':order['total']},token)
    orders=call('/api/orders?limit=10',token=token)['orders']
    assert next(o for o in orders if o['id']==oid)['status']=='paid'
    print('RestaurantOS smoke test: PASS')

if __name__=='__main__':
    try: main()
    except HTTPError as e:
        print('RestaurantOS smoke test: FAIL', e.code, e.read().decode())
        raise
    except Exception as e:
        print('RestaurantOS smoke test: FAIL', e)
        raise
