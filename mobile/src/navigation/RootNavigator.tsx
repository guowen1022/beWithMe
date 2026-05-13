import React from "react";
import { createNativeStackNavigator } from "@react-navigation/native-stack";
import { CanvasScreen } from "../screens/CanvasScreen";
import { SettingsScreen } from "../screens/SettingsScreen";

export type RootStackParamList = {
  Canvas: undefined;
  Settings: undefined;
};

const Stack = createNativeStackNavigator<RootStackParamList>();

export function RootNavigator(): React.ReactElement {
  return (
    <Stack.Navigator screenOptions={{ headerShown: false, contentStyle: { backgroundColor: "#0a0a0f" } }}>
      <Stack.Screen name="Canvas" component={CanvasScreen} />
      <Stack.Screen
        name="Settings"
        component={SettingsScreen}
        options={{ presentation: "modal", animation: "slide_from_bottom" }}
      />
    </Stack.Navigator>
  );
}
