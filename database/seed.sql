INSERT OR IGNORE INTO settings(key, value) VALUES ('restaurant_name', 'RestaurantOS Demo');
INSERT OR IGNORE INTO settings(key, value) VALUES ('currency', 'DZD');
INSERT OR IGNORE INTO settings(key, value) VALUES ('tax_rate', '0');

INSERT OR IGNORE INTO categories(id, name, sort_order) VALUES (1, 'Entrées', 1);
INSERT OR IGNORE INTO categories(id, name, sort_order) VALUES (2, 'Plats', 2);
INSERT OR IGNORE INTO categories(id, name, sort_order) VALUES (3, 'Boissons', 3);
INSERT OR IGNORE INTO categories(id, name, sort_order) VALUES (4, 'Desserts', 4);

INSERT OR IGNORE INTO menu_items(id, category_id, name, description, price) VALUES (1, 1, 'Salade César', 'Poulet grillé, laitue, parmesan', 700);
INSERT OR IGNORE INTO menu_items(id, category_id, name, description, price) VALUES (2, 1, 'Soupe du jour', 'Préparation maison', 450);
INSERT OR IGNORE INTO menu_items(id, category_id, name, description, price) VALUES (3, 2, 'Burger Maison', 'Steak, fromage, salade, sauce', 1200);
INSERT OR IGNORE INTO menu_items(id, category_id, name, description, price) VALUES (4, 2, 'Poulet Grillé', 'Poulet, frites, salade', 1400);
INSERT OR IGNORE INTO menu_items(id, category_id, name, description, price) VALUES (5, 2, 'Pâtes Alfredo', 'Pâtes crémeuses au poulet', 1100);
INSERT OR IGNORE INTO menu_items(id, category_id, name, description, price) VALUES (6, 3, 'Coca-Cola', 'Boisson fraîche', 180);
INSERT OR IGNORE INTO menu_items(id, category_id, name, description, price) VALUES (7, 3, 'Eau minérale', '50 cl', 100);
INSERT OR IGNORE INTO menu_items(id, category_id, name, description, price) VALUES (8, 4, 'Tiramisu', 'Dessert maison', 550);
INSERT OR IGNORE INTO menu_items(id, category_id, name, description, price) VALUES (9, 4, 'Crème caramel', 'Dessert maison', 450);

INSERT OR IGNORE INTO dining_tables(id, table_no, seats, status) VALUES (1, 'T01', 2, 'available');
INSERT OR IGNORE INTO dining_tables(id, table_no, seats, status) VALUES (2, 'T02', 2, 'available');
INSERT OR IGNORE INTO dining_tables(id, table_no, seats, status) VALUES (3, 'T03', 4, 'occupied');
INSERT OR IGNORE INTO dining_tables(id, table_no, seats, status) VALUES (4, 'T04', 4, 'available');
INSERT OR IGNORE INTO dining_tables(id, table_no, seats, status) VALUES (5, 'T05', 6, 'reserved');
INSERT OR IGNORE INTO dining_tables(id, table_no, seats, status) VALUES (6, 'T06', 6, 'available');
INSERT OR IGNORE INTO dining_tables(id, table_no, seats, status) VALUES (7, 'T07', 4, 'available');
INSERT OR IGNORE INTO dining_tables(id, table_no, seats, status) VALUES (8, 'T08', 8, 'available');
