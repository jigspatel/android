package com.common.utils;

public class TodoItem {

    private String id;
    private String title;
    private boolean done;
    private long createdAt;

    public TodoItem(String id, String title, boolean done, long createdAt) {
        this.id = id;
        this.title = title;
        this.done = done;
        this.createdAt = createdAt;
    }

    public String getId() { return id; }
    public String getTitle() { return title; }
    public boolean isDone() { return done; }
    public void setDone(boolean done) { this.done = done; }
    public long getCreatedAt() { return createdAt; }
}
