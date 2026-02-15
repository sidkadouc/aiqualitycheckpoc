const path = require("path");
const fs = require("fs");
const HtmlWebpackPlugin = require("html-webpack-plugin");
const CopyWebpackPlugin = require("copy-webpack-plugin");
const MiniCssExtractPlugin = require("mini-css-extract-plugin");

const devCertsPath = path.join(require("os").homedir(), ".office-addin-dev-certs");

const isProduction = process.env.NODE_ENV === "production";

module.exports = {
  entry: {
    taskpane: "./src/taskpane/taskpane.ts",
    commands: "./src/commands/commands.ts",
  },
  output: {
    path: path.resolve(__dirname, "dist"),
    filename: "[name].js",
    clean: true,
  },
  resolve: {
    extensions: [".ts", ".js"],
  },
  module: {
    rules: [
      {
        test: /\.ts$/,
        use: "ts-loader",
        exclude: /node_modules/,
      },
      {
        test: /\.css$/,
        use: [isProduction ? MiniCssExtractPlugin.loader : "style-loader", "css-loader"],
      },
    ],
  },
  plugins: [
    new HtmlWebpackPlugin({
      template: "./src/taskpane/taskpane.html",
      filename: "taskpane.html",
      chunks: ["taskpane"],
    }),
    new HtmlWebpackPlugin({
      template: "./src/commands/commands.html",
      filename: "commands.html",
      chunks: ["commands"],
    }),
    new CopyWebpackPlugin({
      patterns: [
        { from: "assets", to: "assets" },
        { from: "manifest.xml", to: "manifest.xml" },
      ],
    }),
    ...(isProduction ? [new MiniCssExtractPlugin({ filename: "[name].css" })] : []),
  ],
  devServer: {
    port: 3000,
    server: {
      type: "https",
      options: {
        key: fs.readFileSync(path.join(devCertsPath, "localhost.key")),
        cert: fs.readFileSync(path.join(devCertsPath, "localhost.crt")),
        ca: fs.readFileSync(path.join(devCertsPath, "ca.crt")),
      },
    },
    headers: {
      "Access-Control-Allow-Origin": "*",
    },
    static: {
      directory: path.resolve(__dirname, "dist"),
    },
    hot: true,
  },
  devtool: isProduction ? "source-map" : "eval-source-map",
};
