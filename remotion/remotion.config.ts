import { Config } from "@remotion/cli/config";
import { webpackOverride } from "./webpack-override.mjs";

Config.setVideoImageFormat("jpeg");
Config.setOverwriteOutput(true);
Config.overrideWebpackConfig(webpackOverride);
