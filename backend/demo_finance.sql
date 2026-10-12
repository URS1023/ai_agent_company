CREATE DATABASE IF NOT EXISTS demo_finance DEFAULT CHARACTER SET utf8mb4;
USE demo_finance;
CREATE TABLE IF NOT EXISTS sales (
  id INT AUTO_INCREMENT PRIMARY KEY,
  project_name VARCHAR(100) NOT NULL,
  region VARCHAR(50) NOT NULL,
  amount DECIMAL(14,2) NOT NULL,
  sale_date DATE NOT NULL
);
INSERT INTO sales (project_name, region, amount, sale_date) VALUES
('北京XX项目','华北',1250000.00,'2026-01-15'),
('北京XX项目','华北',980000.00,'2026-02-20'),
('上海YY项目','华东',2100000.00,'2026-03-10'),
('广州ZZ项目','华南',780000.00,'2026-04-05'),
('深圳AA项目','华南',1650000.00,'2026-05-18'),
('成都BB项目','西南',860000.00,'2026-06-12'),
('北京XX项目','华北',1420000.00,'2026-07-08');
CREATE TABLE IF NOT EXISTS projects (
  id INT AUTO_INCREMENT PRIMARY KEY,
  project_code VARCHAR(50) NOT NULL,
  project_name VARCHAR(100) NOT NULL,
  template_id VARCHAR(50) NOT NULL
);
INSERT INTO projects (project_code, project_name, template_id) VALUES
('P001823','北京XX项目','OP-02'),
('P002211','上海YY项目','OP-01');
