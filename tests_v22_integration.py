from urllib.request import Request, urlopen
from urllib.error import HTTPError
import json
BASE='http://127.0.0.1:4000'
def call(path, method='GET', data=None, token=''):
    body=json.dumps(data).encode() if data is not None else None
    headers={'Content-Type':'application/json'}
    if token: headers['X-Session-Token']=token
    req=Request(BASE+path,data=body,method=method,headers=headers)
    try:
        with urlopen(req,timeout=7) as r: return r.status,json.loads(r.read().decode())
    except HTTPError as e:
        raw=e.read().decode()
        try: payload=json.loads(raw)
        except: payload={'raw':raw}
        return e.code,payload

def exp(label,status,payload,code=200):
    assert status==code, f'{label}: HTTP {status} {payload}'
    if code==200: assert payload.get('ok') is not False, f'{label}: {payload}'
    return payload

exp('health',*call('/api/health'))
_,h=call('/api/health'); assert h['version']=='2.2'
_,l=call('/api/login','POST',{'email':'admin@restaurantos.local','password':'Admin@123'}); exp('login',200,l); token=l['token']
# Reads
for path in ['/api/me','/api/settings','/api/menu','/api/tables','/api/orders?limit=50','/api/dashboard','/api/inventory','/api/inventory/movements','/api/suppliers','/api/purchases','/api/expenses','/api/payments','/api/reports/summary?from=2026-09-01&to=2026-09-30','/api/kitchen','/api/customers','/api/reservations?date=2026-09-25','/api/cash','/api/recipes','/api/debts','/api/audit','/api/users']:
    s,p=call(path,token=token); exp(path,s,p)
# Create/update category
s,p=call('/api/categories','POST',{'name':'Test Category','sort_order':99},token); exp('category create',s,p); catid=p['id']
# Customer
s,p=call('/api/customers','POST',{'name':'Test Customer','phone':'0555000000','email':'test@example.local'},token); exp('customer create',s,p); cid=p['id']
# New menu item via category
s,p=call('/api/menu/items','POST',{'name':'Test Dish','description':'integration','price':100,'category_id':catid,'active':True},token); exp('menu create',s,p); mid=p['id']
# Inventory item and recipe BEFORE sale
s,p=call('/api/inventory/items','POST',{'sku':'TST-RAW','name':'Test Raw','unit':'kg','current_stock':5,'min_stock':1,'cost_price':50},token); exp('inventory create',s,p); iid=p['id']
s,p=call('/api/recipes','POST',{'menu_item_id':mid,'inventory_item_id':iid,'quantity':0.5},token); exp('recipe create',s,p)
# Purchase adds 2kg
s,p=call('/api/purchases','POST',{'supplier_id':None,'invoice_no':'T-PUR','items':[{'inventory_item_id':iid,'quantity':2,'unit_cost':60}]},token); exp('purchase create',s,p)
# Table add/update
s,p=call('/api/tables','POST',{'table_no':'T99','seats':4,'status':'available'},token); exp('table create',s,p); tid=p['id']
s,p=call('/api/tables','POST',{'id':tid,'table_no':'T99','seats':6,'status':'available'},token); exp('table update',s,p)
# Cash open
s,p=call('/api/cash/open','POST',{'opening_amount':1000,'notes':'Integration'},token); exp('cash open',s,p)
# Discounted order
s,p=call('/api/orders','POST',{'table_id':tid,'customer_id':cid,'discount':10,'items':[{'menu_item_id':mid,'quantity':1}]},token); exp('discount order',s,p)
assert p['discount']==10 and p['subtotal']==100 and p['total']==90
order_id=p['order_id']; total=p['total']
# Kitchen lifecycle
for st in ('preparing','ready'):
    s,p=call('/api/orders/status','POST',{'order_id':order_id,'status':st},token); exp('order '+st,s,p)
# Payment closes order and consumes 0.5kg
s,p=call('/api/payments','POST',{'order_id':order_id,'payment_method':'cash','amount':total},token); exp('payment',s,p)
s,p=call('/api/orders?limit=50',token=token); exp('orders after payment',s,p); od=next(x for x in p['orders'] if x['id']==order_id); assert od['status']=='paid'
s,p=call('/api/inventory',token=token); exp('inventory after sale',s,p); inv=next(x for x in p['items'] if x['id']==iid); assert abs(inv['current_stock']-6.5)<0.001
# Reservation
s,p=call('/api/reservations','POST',{'customer_id':cid,'table_id':2,'reservation_date':'2026-09-25','reservation_time':'19:30','guests':4,'notes':'Integration'},token); exp('reservation create',s,p); rid=p['id']
s,p=call('/api/reservations/status','POST',{'id':rid,'status':'confirmed'},token); exp('reservation status',s,p)
# Expense
s,p=call('/api/expenses','POST',{'category':'Testing','description':'Test expense','amount':50,'expense_date':'2026-09-24','paid':True},token); exp('expense create',s,p)
# Debt
s,p=call('/api/debts','POST',{'debt_type':'receivable','party_name':'Test Customer','amount':500,'due_date':'2026-10-01','reference':'T-DEBT'},token); exp('debt create',s,p); did=p['id']
s,p=call('/api/debts/payment','POST',{'id':did,'amount':200},token); exp('debt payment',s,p); assert p['status']=='partial' and abs(p['remaining']-300)<.01
# Reports
s,p=call('/api/reports/summary?from=2026-09-01&to=2026-09-30',token=token); exp('report',s,p); assert p['summary']['sales']>=90 and 'gross_profit' in p['summary']
# Staff auth + permissions
s,p=call('/api/users','POST',{'email':'tester@restaurantos.local','display_name':'Tester','password':'TestPass123','role':'cashier'},token); exp('user create',s,p)
s,p=call('/api/login','POST',{'email':'tester@restaurantos.local','password':'TestPass123'}); exp('staff login',s,p); staff=p['token']; assert p['user']['role']=='cashier'
s,p=call('/api/users',token=staff); exp('users permissions',s,p,403)
# Backup and download
s,p=call('/api/backup','POST',{},token); exp('backup',s,p); fname=p['filename']
req=Request(BASE+p['url'],headers={'X-Session-Token':token})
with urlopen(req,timeout=7) as r:
    assert r.status==200 and len(r.read())>0
# Settings round trip
s,p=call('/api/settings','POST',{'restaurant_name':'RestaurantOS Integration','tax_rate':'10'},token); exp('settings',s,p); assert p['settings']['tax_rate']=='10'
# Archive test entities
for path,data in [('/api/recipes/delete',{'id':(call('/api/recipes?menu_item_id='+str(mid),token=token)[1]['recipes'][0]['id'])}),('/api/menu/items/delete',{'id':mid}),('/api/categories/delete',{'id':catid}),('/api/customers/delete',{'id':cid}),('/api/tables/delete',{'id':tid})]:
    s,p=call(path,'POST',data,token); exp('archive '+path,s,p)
# Close cash and logout
s,p=call('/api/cash',token=token); exp('cash read',s,p); expected=p['expected_cash']
s,p=call('/api/cash/close','POST',{'closing_amount':expected,'notes':'close'},token); exp('cash close',s,p); assert abs(p['difference'])<.01
s,p=call('/api/logout','POST',None,token); exp('logout',s,p)
s,p=call('/api/me',token=token); exp('session invalidation',s,p,401)
print('RestaurantOS 2.2 integration: PASS')
