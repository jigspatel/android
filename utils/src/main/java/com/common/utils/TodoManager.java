package com.common.utils;

import android.content.Context;
import android.content.SharedPreferences;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.List;
import java.util.UUID;

public class TodoManager {

    private static final String PREFS_NAME = "todo_prefs";
    private static final String KEY_TODOS = "todos";

    public static void addTodo(Context context, String title) {
        List<TodoItem> todos = getTodos(context);
        todos.add(new TodoItem(UUID.randomUUID().toString(), title, false, System.currentTimeMillis()));
        saveTodos(context, todos);
    }

    public static void removeTodo(Context context, String id) {
        List<TodoItem> todos = getTodos(context);
        for (int i = 0; i < todos.size(); i++) {
            if (todos.get(i).getId().equals(id)) {
                todos.remove(i);
                break;
            }
        }
        saveTodos(context, todos);
    }

    public static void toggleTodo(Context context, String id) {
        List<TodoItem> todos = getTodos(context);
        for (TodoItem item : todos) {
            if (item.getId().equals(id)) {
                item.setDone(!item.isDone());
                break;
            }
        }
        saveTodos(context, todos);
    }

    public static List<TodoItem> getTodos(Context context) {
        SharedPreferences prefs = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE);
        String json = prefs.getString(KEY_TODOS, "[]");
        List<TodoItem> todos = new ArrayList<>();
        try {
            JSONArray array = new JSONArray(json);
            for (int i = 0; i < array.length(); i++) {
                JSONObject obj = array.getJSONObject(i);
                todos.add(new TodoItem(
                        obj.getString("id"),
                        obj.getString("title"),
                        obj.getBoolean("done"),
                        obj.getLong("createdAt")
                ));
            }
        } catch (JSONException e) {
            // return empty list on parse error
        }
        return todos;
    }

    private static void saveTodos(Context context, List<TodoItem> todos) {
        JSONArray array = new JSONArray();
        for (TodoItem item : todos) {
            JSONObject obj = new JSONObject();
            try {
                obj.put("id", item.getId());
                obj.put("title", item.getTitle());
                obj.put("done", item.isDone());
                obj.put("createdAt", item.getCreatedAt());
                array.put(obj);
            } catch (JSONException e) {
                // skip item on error
            }
        }
        context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
                .edit()
                .putString(KEY_TODOS, array.toString())
                .commit();
    }
}
