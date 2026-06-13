package com.common.example;

import android.app.Activity;
import android.content.Context;
import android.graphics.Paint;
import android.os.Bundle;
import android.view.View;
import android.view.ViewGroup;
import android.widget.AdapterView;
import android.widget.ArrayAdapter;
import android.widget.Button;
import android.widget.EditText;
import android.widget.ListView;
import android.widget.TextView;
import android.widget.Toast;

import com.common.utils.R;
import com.common.utils.TodoItem;
import com.common.utils.TodoManager;

import java.util.List;

public class TodoActivity extends Activity {

    private ListView listView;
    private EditText etTitle;
    private List<TodoItem> todos;
    private TodoAdapter adapter;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_todo);

        listView = (ListView) findViewById(R.id.todo_list);
        etTitle  = (EditText) findViewById(R.id.todo_et_title);
        Button btnAdd = (Button) findViewById(R.id.todo_btn_add);

        todos   = TodoManager.getTodos(this);
        adapter = new TodoAdapter(this, todos);
        listView.setAdapter(adapter);

        btnAdd.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                String title = etTitle.getText().toString().trim();
                if (title.isEmpty()) {
                    Toast.makeText(TodoActivity.this, "Enter a task", Toast.LENGTH_SHORT).show();
                    return;
                }
                TodoManager.addTodo(TodoActivity.this, title);
                etTitle.setText("");
                refresh();
            }
        });

        listView.setOnItemClickListener(new AdapterView.OnItemClickListener() {
            @Override
            public void onItemClick(AdapterView<?> parent, View view, int position, long id) {
                TodoManager.toggleTodo(TodoActivity.this, todos.get(position).getId());
                refresh();
            }
        });

        listView.setOnItemLongClickListener(new AdapterView.OnItemLongClickListener() {
            @Override
            public boolean onItemLongClick(AdapterView<?> parent, View view, int position, long id) {
                TodoManager.removeTodo(TodoActivity.this, todos.get(position).getId());
                refresh();
                return true;
            }
        });
    }

    private void refresh() {
        todos.clear();
        todos.addAll(TodoManager.getTodos(this));
        adapter.notifyDataSetChanged();
    }

    private static class TodoAdapter extends ArrayAdapter<TodoItem> {

        TodoAdapter(Context context, List<TodoItem> items) {
            super(context, android.R.layout.simple_list_item_1, items);
        }

        @Override
        public View getView(int position, View convertView, ViewGroup parent) {
            View view = super.getView(position, convertView, parent);
            TextView tv = (TextView) view.findViewById(android.R.id.text1);
            TodoItem item = getItem(position);
            tv.setText(item.getTitle());
            if (item.isDone()) {
                tv.setPaintFlags(tv.getPaintFlags() | Paint.STRIKE_THRU_TEXT_FLAG);
                tv.setTextColor(0xFF888888);
            } else {
                tv.setPaintFlags(tv.getPaintFlags() & ~Paint.STRIKE_THRU_TEXT_FLAG);
                tv.setTextColor(0xFF000000);
            }
            return view;
        }
    }
}
