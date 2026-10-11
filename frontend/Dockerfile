# Web image: builds the SPA, serves it with nginx, proxies /api to the API
# service (single origin -> no CORS, and security headers set in one place).
# Not build-tested: base-image registry was unreachable in the authoring sandbox.
FROM node:22-slim AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
# Empty = same-origin API calls through the nginx proxy below.
ARG VITE_API_BASE=""
ENV VITE_API_BASE=$VITE_API_BASE
RUN npm run build

FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
