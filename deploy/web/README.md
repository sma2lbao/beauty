# Web 部署(静态 SPA + Nginx + BFF)

M0-01 起,web 不走 Vercel:

- `apps/web` 只构建静态产物 `apps/web/dist`;
- 同一域名由 Nginx 托管静态文件,并把 `/api/` 反代到 FastAPI BFF(`services/bff`,:8007);
- SPA 深链由 `try_files $uri $uri/ /index.html` 兜底。

## 本地验证

```sh
# 1) 构建 SPA
npx nx build @beauty/web

# 2) 起 BFF
npx nx run bff:serve

# 3) 用 Nginx 托管(示例,按本机路径调整)
#    root 指向 apps/web/dist,upstream 改成 127.0.0.1:8007
nginx -c "$PWD/deploy/web/nginx.conf"  # 生产用 conf.d 挂载
curl -i http://127.0.0.1/api/health
curl -i http://127.0.0.1/runs   # 应为 index.html(200)
```

## 说明

- 生产环境使用容器内服务名 `bff:8007`;裸机/Nginx 直接装在宿主机时把 `deploy/web/nginx.conf` 的 upstream 改成 `127.0.0.1:8007`。
- M0-01 的 BFF 只有 `/api/health`;其他 `/api/*` 业务代理在 M0-06 接入。
- 若未来 web 与 BFF 不同域,需要补 CORS + Cookie `SameSite` 策略;当前默认同域。
