# 部署说明

## 架构说明

- **后端**：运行在 `http://localhost:8000`，提供 API 服务
- **前端**：运行在 `http://localhost:3000`，提供 Web 界面

前端通过配置的 `apiUrl` 连接到后端 API。

## 部署方式

### 方式一：一键部署（推荐）

同时启动前端和后端服务。

**Windows:**
```bash
deploy.bat
```

**Linux/Mac:**
```bash
chmod +x deploy.sh
./deploy.sh
```

### 方式二：分别部署

#### 1. 部署后端

**Windows:**
```bash
deploy_backend.bat
```

**Linux/Mac:**
```bash
chmod +x deploy_backend.sh
./deploy_backend.sh
```

后端将运行在 `http://localhost:8000`

#### 2. 部署前端

**Windows:**
```bash
deploy_frontend.bat
```

**Linux/Mac:**
```bash
chmod +x deploy_frontend.sh
./deploy_frontend.sh
```

前端将运行在 `http://localhost:3000`

## 访问地址

部署完成后，访问：**http://localhost:3000**

## 端口说明

- **8000**：后端 API 服务
- **3000**：前端 Web 服务

如需修改端口，请分别修改：
- 后端：`main.py` 中的 uvicorn 配置
- 前端：`web/src/constants/common-env.ts` 中的 `apiUrl` 配置

## 停止服务

按 `Ctrl+C` 停止服务。

如果使用一键部署脚本（Windows），需要分别关闭两个命令行窗口。
