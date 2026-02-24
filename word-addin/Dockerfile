# ───────────────────────────────────────────────────────────────
# Dockerfile — OECD Word Add-in (static SPA served via nginx)
# Deploy to Azure Container Apps
# ───────────────────────────────────────────────────────────────

# ── Build stage ────────────────────────────────────────────────
FROM node:20-alpine AS build

WORKDIR /app

# Install dependencies
COPY package.json package-lock.json* ./
RUN npm ci --ignore-scripts

# Copy source & build
COPY tsconfig.json webpack.config.js ./
COPY src/ ./src/
COPY assets/ ./assets/
COPY manifest.xml ./

# Build for production (webpack outputs to dist/)
ENV NODE_ENV=production
RUN npm run build

# ── Runtime stage ──────────────────────────────────────────────
FROM nginx:1.27-alpine

# Remove default nginx config
RUN rm /etc/nginx/conf.d/default.conf

# Custom nginx config for SPA + CORS headers required by Office.js
COPY <<'EOF' /etc/nginx/conf.d/app.conf
server {
    listen       8080;
    server_name  _;

    root   /usr/share/nginx/html;
    index  taskpane.html;

    # Security headers
    add_header X-Frame-Options "ALLOWALL" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header Referrer-Policy "no-referrer-when-downgrade" always;

    # CORS for Office.js add-in
    add_header Access-Control-Allow-Origin "*" always;
    add_header Access-Control-Allow-Methods "GET, OPTIONS" always;
    add_header Access-Control-Allow-Headers "*" always;

    location / {
        try_files $uri $uri/ /taskpane.html;
    }

    # Cache static assets
    location ~* \.(js|css|png|jpg|jpeg|gif|ico|svg|woff|woff2|ttf|eot)$ {
        expires 7d;
        add_header Cache-Control "public, immutable";
    }

    # Health check endpoint
    location /healthz {
        access_log off;
        return 200 'ok';
        add_header Content-Type text/plain;
    }
}
EOF

# Copy built assets from build stage
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD wget -qO- http://localhost:8080/healthz || exit 1

CMD ["nginx", "-g", "daemon off;"]
