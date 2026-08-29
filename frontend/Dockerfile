FROM node:22-slim

WORKDIR /srv

# The lockfile, and `npm ci` rather than `npm install`: the image resolved its own
# dependency tree from package.json alone, so the container could run versions no
# CI job had ever tested.
COPY package.json package-lock.json ./
RUN npm ci

COPY . .

EXPOSE 5173

CMD ["npm", "run", "dev"]
