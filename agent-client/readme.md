# 我的项目

## 问答

![页面](./resources/1.png)

![页面](./resources/2.png)

## 专家团

![页面](./resources/3.png)

![页面](./resources/4.png)


# 前端端本地运行所需标准最小环境
Nodejs >= 18.18.2  
npm 8.9.2  

# 开发环境
Visual Studio Code

# 运行方式
cd agent-electron-app

# 开发模式
npm run dev

# 构建
npm run build

# 打包安装包（需要 Windows，且 resources/ 下有图标文件）
npm run package

npx electron-builder --x64 --win nsis
