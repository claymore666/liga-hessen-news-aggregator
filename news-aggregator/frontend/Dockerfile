# Frontend Dockerfile - Multi-stage build

# Stage 1: Build
FROM node:20-alpine3.21 AS builder

WORKDIR /app

# Copy package files
COPY package.json package-lock.json ./

# Install dependencies
RUN npm ci

# Copy source code
COPY . .

# Build the application
RUN npm run build

# Stage 2: Production
FROM nginx:1.27-alpine

# Copy custom nginx config
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Copy built assets from builder stage
COPY --from=builder /app/dist /usr/share/nginx/html

# Ensure nginx can write to required directories
RUN chown -R nginx:nginx /var/cache/nginx /var/log/nginx /run && \
    chmod -R 755 /var/cache/nginx /var/log/nginx /run

USER nginx

# Expose port
EXPOSE 80

# Health check (use 127.0.0.1 to avoid IPv6 resolution issues)
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://127.0.0.1:80/ || exit 1

# Start nginx
CMD ["nginx", "-g", "daemon off;"]
